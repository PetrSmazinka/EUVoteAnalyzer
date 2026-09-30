"""
Lightweight ORM layer for MySQL/MariaDB, with schema-driven table management.

DB             – process-wide singleton connection wrapper; executes arbitrary
                 parameterised SQL and abstracts away cursor lifecycle.
DBColumn       – represents a single table column, translating JSON-Schema types
                 to MySQL DDL types.
DBObject       – represents a database table; builds CREATE/DROP/INSERT DDL and
                 recursively persists nested JSON records via foreign-key links.
DBObjects      – global registry of all DBObject instances, keyed by table name.
SchemaExplorer – walks a JSON Schema document and materialises the corresponding
                 DBObject graph, resolving foreign-key and self-reference edges.
"""

import inspect
import json
import mysql.connector
from jsonschema import validate, ValidationError
from typing import Dict, List, Any, Tuple, cast, Optional
from mysql.connector import Error

from EUVoteAnalyzer.core.common import JsonSchema, INDENTATION
from EUVoteAnalyzer.core.utils import GeneratorManager, ProgressPrint
from EUVoteAnalyzer.core.secret import DB_DB, DB_PASSWORD, DB_PORT, DB_SERVER, DB_USER

META_SCHEMA : Dict[str, Any]|bool = {
    "type": "object",
    "properties": {
        "type": {"type": "string"},
        "items": {
            "type": "object",
            "properties": {
                "type": {"const": "object"},
                "properties": {"type": "object"}
            },
            "required": ["type", "properties"]
        }
    },
    "required": ["type", "items"]
}

class DB:
    """
    Process-wide MySQL/MariaDB connection singleton.

    A single shared connection is reused across all :class:`DB` instances.
    The connection is re-established automatically if it drops.  All DML
    operations (INSERT, UPDATE, DELETE) are committed immediately; SELECT
    results are returned as a list of ``dict`` rows.

    The database is created automatically if it does not exist yet.
    """

    _shared_connection = None
    _port_override: Optional[int] = None

    @classmethod
    def use_test_port(cls, port: int = 3331) -> None:
        """
        Redirect the shared connection to a test database on *port*.

        Closes the existing connection so the next operation reconnects to the
        test instance.

        Args:
            port: TCP port of the test database server (default 3331).
        """
        cls._port_override = port
        cls._shared_connection = None

    def __init__(self, host : str = DB_SERVER, port : int = DB_PORT, user : str = DB_USER, password : str = DB_PASSWORD, database : str = DB_DB):
        """
        Args:
            host:     Database server hostname or IP.
            port:     TCP port (overridden by :meth:`use_test_port` when set).
            user:     MySQL username.
            password: MySQL password.
            database: Target schema name; created automatically if absent.
        """
        self.config = {
            'host': host,
            'user': user,
            'password': password,
            'database': database,
            'port': str(port)
        }
        self._ensure_connection()

    def _ensure_connection(self):
        """Open (or reopen) the shared connection, creating the database schema if needed."""
        if DB._port_override is not None:
            self.config['port'] = str(DB._port_override)
        if DB._shared_connection is None or not DB._shared_connection.is_connected():
            try:
                # Create DB if not exist
                conn = mysql.connector.connect(
                    host=self.config['host'],
                    user=self.config['user'],
                    password=self.config['password'],
                    port=self.config['port']
                )
                cursor = conn.cursor()
                cursor.execute(f"CREATE DATABASE IF NOT EXISTS {self.config['database']}")
                cursor.close()
                conn.close()

                # Create connection
                DB._shared_connection = mysql.connector.connect(**self.config, use_pure=True, consume_results=True)
            except Error as e:
                ProgressPrint.log(f"Error when connecting to MySQL DB ({e})", "ERR!")

    @property
    def connection(self):
        """The active ``mysql.connector`` connection object."""
        return DB._shared_connection

    def query(self, query: str, values: Dict[str, Any] = {}) -> List[Any]|bool|int:
        """
        Execute a parameterised SQL statement and return the result.

        SELECT queries return a list of row dicts.  INSERT returns the
        ``lastrowid`` (int).  All other DML returns ``True`` on success.
        Returns ``False`` on any connection or SQL error.

        Args:
            query:  Parameterised SQL string (named ``%(key)s`` placeholders).
            values: Mapping of parameter names to values.

        Returns:
            List of row dicts for SELECT; ``int`` for INSERT; ``True`` for other
            DML; ``False`` on error.
        """
        if self.connection is None:
            ProgressPrint.log("DB connection not initialized", "ERR!")
            return False
        cursor = self.connection.cursor(dictionary=True, buffered=True)
        try:
            cursor.execute(query, values)
            # SELECT
            if cursor.description: 
                result = cursor.fetchall()
                # Vyčištění případných dalších sad výsledků
                while self.connection.unread_result:
                    self.connection.get_rows()
            else:
                # INSERT, UPDATE, DELETE
                # INSERT
                if query.strip().upper().startswith("INSERT"):
                    result = cast(int, cursor.lastrowid)
                else:
                    result = True
                while self.connection.unread_result:
                    self.connection.get_rows()
                self.connection.commit()
            return result
        except Error as e:
            line=""
            frame = inspect.currentframe()
            if frame and frame.f_back:
                line = str(frame.f_back.f_lineno)
            ProgressPrint.log(f"SQL Error: {e}", "ERR!", line)
            return False
        finally:
            cursor.close()

    def query_many(self, query: str, values_list: List[tuple]) -> bool:
        """
        Execute a single parameterised statement for each row in *values_list*.

        Uses ``cursor.executemany`` for efficiency.  All rows are committed in
        a single transaction.

        Args:
            query:       Parameterised SQL with positional ``%s`` placeholders.
            values_list: Sequence of tuples, one per row.

        Returns:
            ``True`` on success, ``False`` on any error.
        """
        if self.connection is None:
            ProgressPrint.log("DB connection not initialized", "ERR!")
            return False
        cursor = self.connection.cursor(buffered=True)
        try:
            cursor.executemany(query, values_list)
            while self.connection.unread_result:
                self.connection.get_rows()
            self.connection.commit()
            return True
        except Error as e:
            line = ""
            frame = inspect.currentframe()
            if frame and frame.f_back:
                line = str(frame.f_back.f_lineno)
            ProgressPrint.log(f"SQL Error: {e}", "ERR!", line)
            return False
        finally:
            cursor.close()

class DBColumn:
    """
    Descriptor for a single database column, derived from a JSON-Schema property.

    Translates JSON-Schema type keywords (``string``, ``integer``, ``boolean``,
    ``oneOf``, ``pattern``, etc.) to their MySQL DDL equivalents.  Also carries
    foreign-key reference metadata consumed by :class:`DBObject`.
    """

    def __init__(self, column_type: str|None, name: str|int|bool, max_length: int|None = None, pattern: str|None = None, \
                 description: str|None = None, one_of: List[Dict[str, str|bool]]|None = None, required: bool = False, \
                 reference: Tuple[str, str]|None = None, primary: bool|None = None, generated: bool|None = None):
        """
        Args:
            column_type:  JSON-Schema ``"type"`` value, or ``None`` when ``one_of`` is used.
            name:         Column identifier.
            max_length:   ``maxLength`` constraint used to choose VARCHAR vs TEXT.
            pattern:      ``pattern`` constraint; specific regexes map to DATE / YEAR / DATETIME.
            description:  Human-readable column comment written to DDL.
            one_of:       ``oneOf`` schema alternatives; drives BOOLEAN / VARCHAR selection.
            required:     When ``True``, the column is declared ``NOT NULL``.
            reference:    ``(table, column)`` pair for a FOREIGN KEY constraint.
            primary:      Marks this column as part of the primary key.
            generated:    When ``True``, adds ``AUTO_INCREMENT`` to the DDL.
        """
        self._type = DBColumn.db_type(column_type, max_length, pattern, one_of)
        self._required = required
        self._name = str(name)
        self._description = description
        self._reference = reference
        self._primary = primary
        self._generated = generated
    
    @property
    def type(self):
        return self._type
    
    @property
    def name(self):
        return self._name
    
    @property
    def required(self):
        return self._required
    
    @property
    def description(self):
        return self._description

    @property
    def reference(self):
        return self._reference
    
    @reference.setter
    def reference(self, value: Tuple[str, str]):
        self._reference = value

    @property
    def primary(self):
        return self._primary
    
    @property
    def generated(self):
        return self._generated

    @staticmethod
    def db_type(column_type : str|None, column_max_length: int|None = None, column_pattern: str|None = None, column_one_of: List[Dict[str, str|bool]]|None = None) -> str:
        """
        Derive the MySQL column type from JSON-Schema type metadata.

        Args:
            column_type:       JSON-Schema ``"type"`` string, or ``None``.
            column_max_length: ``maxLength`` value; selects VARCHAR vs TEXT.
            column_pattern:    Regex pattern; known date-time patterns select
                               DATE / YEAR / DATETIME.
            column_one_of:     ``oneOf`` alternatives for union-type resolution.

        Returns:
            A MySQL DDL type string such as ``"VARCHAR(100)"``, ``"INTEGER"``,
            ``"BOOLEAN"``, ``"DATE"``, or ``"TEXT"``.
        """
        if column_type is None and column_one_of is not None:
            if len(column_one_of) <= 2 and column_one_of[0].get("const") is not None and isinstance(column_one_of[0].get("const"), bool):
                return "BOOLEAN"
            elif column_one_of[0].get("type") is not None and isinstance(column_one_of[0].get("type"), str):
                return DBColumn.db_type(cast(str,column_one_of[0].get("type")))
            elif column_one_of[0].get("const") is not None and isinstance(column_one_of[0].get("const"), str):
                return f"VARCHAR({len(cast(str,column_one_of[0].get("const")))})"
            else:
                return "TEXT"
        elif column_type == "string":
            if column_pattern is not None and column_pattern == "^[0-9]{4}-[0-9]{2}-[0-9]{2}$":
                return "DATE"
            elif column_pattern is not None and column_pattern == "^[0-9]{4}$":
                return "YEAR"
            elif column_pattern is not None and column_pattern == "^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}$":
                return "DATETIME"
            elif column_max_length is None or column_max_length > 255:
                return "TEXT"
            return f"VARCHAR({column_max_length})"
        elif column_type == "integer" or column_type == "int":
            return "INTEGER"
        elif column_type == "float":
            return "FLOAT"
        elif column_type == "boolean" or column_type == "bool":
            return "BOOLEAN"
        return "TEXT"
    
    def __str__(self) -> str:
        return json.dumps({
            "name": self.name,
            "type": self.type,
            "required": self.required,
            "reference": self.reference,
            "description": self.description
        })

class DBObject:
    """
    Represents a database table and orchestrates its entire DDL and DML lifecycle.

    Columns are accumulated via :meth:`add`; foreign-key constraints and child-table
    relationships are tracked separately.  :meth:`create` emits the ``CREATE TABLE``
    DDL, :meth:`save` inserts a single record and recurses into nested lists, and
    :meth:`save_many` bulk-inserts rows with ``ON DUPLICATE KEY UPDATE`` semantics.
    """

    def __init__(self, name: str, description: str|None = None, db : DB = DB()):
        """
        Args:
            name:        Logical table name; deduplicated by :class:`GeneratorManager`.
            description: Human-readable table comment written to DDL.
            db:          :class:`DB` instance used for all DML operations.
        """
        self._name = GeneratorManager.get(name)
        self._description = description
        self._columns : List[DBColumn] = []
        self._fk_constraints : List[Tuple[str, Tuple[str, str]]] = [] # (column_name (foreign table, foreign column))
        self._references : List[Tuple[str, str]] = []  # (column_name, foreign table)
        self._primary_key : List[str] = []
        self._indexes : List[Tuple[str, List[str]]] = []  # (index_name, [col1, col2, ...])
        self.__readonly = False
        self._db = db

    def set_readonly(self):
        """Freeze this object so no further columns or references can be added."""
        self.__readonly = True

    def is_readonly(self):
        """Return ``True`` and log a warning if the object is in read-only mode."""
        if self.__readonly:
            ProgressPrint.log(f"DB entity {self._name} is already set as readonly", "WARN")
            return True
        return False

    def add(self, column: DBColumn):
        """
        Append *column* to this table's column list.

        If the column carries a foreign-key reference it is also registered in
        the FK constraint list.

        Args:
            column: :class:`DBColumn` to add.
        """
        if self.is_readonly():
            return
        self._columns.append(column)
        if column.reference is not None:
            self._fk_constraints.append((column.name, column.reference))

    def add_reference(self, referencer: Tuple[str, str]):
        """
        Register a parent → child relationship without adding a column.

        Used by :class:`SchemaExplorer` to track which sub-tables are owned by
        this object so they can be populated during :meth:`save`.

        Args:
            referencer: ``(child_column_name, child_table_name)`` tuple.
        """
        if self.is_readonly():
            return
        self._references.append(referencer)
    
    @property
    def name(self) -> str:
        return self._name
    
    @property
    def primary_key(self) -> List[str]:
        return self._primary_key

    def find_primary_key(self):
        """
        Resolve and cache the primary-key column list.

        If no column is marked ``primary``, an auto-increment ``id`` column is
        appended and used as the sole primary key.
        """
        primary_keys : List[str] = []
        for column in self._columns:
            if column.primary:
                primary_keys.append(column.name)
        if len(primary_keys) == 0:
            if "id" not in map(lambda x: x.name, self._columns):
                self.add(DBColumn(column_type="integer", name="id", description="Auto generated id", required=True, generated=True))
            primary_keys = ["id"]
        self._primary_key = primary_keys
    
    def find_references(self) -> List[str]:
        """Return the names of all tables that this table's FK columns point to (excluding self)."""
        refs :List[str]= []
        for _, ref_tuple in self._fk_constraints:
            referenced_table = ref_tuple[0]
            if referenced_table not in refs and referenced_table != self._name:
                refs.append(referenced_table)
        return refs
    
    def find_self_referece(self) -> List[str]:
        """Return the names of FK constraints where this table references itself."""
        refs :List[str]= []
        for _, ref_tuple in self._fk_constraints:
            referenced_table = ref_tuple[0]
            if referenced_table == self._name:
                refs.append(referenced_table)
        return refs
    
    def find_referencers(self) -> List[str]:
        """
        Return the names of all tables that hold a FK pointing to this table.

        Combines explicitly registered references with a scan of all other
        :class:`DBObject` instances in the global registry.
        """
        refs = [table for (_, table) in self._references]
        db_objects = cast(Dict[str, DBObject], DBObjects.get())
        for table_name, db_obj in db_objects.items():
            if table_name == self._name or table_name in refs:
                continue
            for _, ref_tuple in db_obj._fk_constraints:
                if ref_tuple[0] == self._name:
                    refs.append(table_name)
                    break
        return refs
    
    def create(self) -> str:
        """Return the full ``CREATE TABLE IF NOT EXISTS`` DDL for this table."""
        pk_list = ", ".join(f"`{column}`" for column in self._primary_key)
        columns = ",\n".join(f"{' '*INDENTATION}`{col.name}` {col.type} {"NOT " if col.required or col.primary else ""}NULL{" AUTO_INCREMENT" if col.generated else ""}{f" COMMENT '{col.description.replace("'", "`")}'" if col.description else ""}" for col in self._columns)
        fk_list = [con for con in self._fk_constraints if con[1][0] != self._name]
        fk_constraints = ",\n".join(f"{' '*INDENTATION}FOREIGN KEY (`{con[0]}`) REFERENCES `{con[1][0]}`(`{con[1][1]}`)" for con in fk_list)
        comment = f" COMMENT '{self._description.replace("'", "`")}'" if self._description is not None else ""
        result = f"""
CREATE TABLE IF NOT EXISTS `{self._name}` (
{columns},
{' '*INDENTATION}PRIMARY KEY({pk_list}){f",\n{fk_constraints}" if fk_constraints != "" else ""}
){comment};
"""
        return result
    def create_alter(self) -> List[str]:
        """
        Return ``ALTER TABLE`` statements that add self-referencing FK constraints.

        Self-references cannot be expressed in the original ``CREATE TABLE``
        because the table must exist first; they are applied in a second pass.
        """
        alters :List[str] = []
        self_refs = [con for con in self._fk_constraints if con[1][0] == self._name]
        for con in self_refs:
            sql = f"ALTER TABLE `{self._name}` ADD FOREIGN KEY (`{con[0]}`) REFERENCES `{self._name}`(`{con[1][1]}`);"
            alters.append(sql)
        return alters

    def add_index(self, name: str, columns: List[str]):
        """
        Register a named composite index for later DDL generation.

        Args:
            name:    Index identifier used in the ALTER TABLE statement.
            columns: Ordered list of column names to include in the index.
        """
        self._indexes.append((name, columns))

    def create_indexes(self) -> List[Tuple[str, str]]:
        """Return (index_name, ALTER TABLE SQL) pairs for all declared indexes."""
        result: List[Tuple[str, str]] = []
        for index_name, cols in self._indexes:
            cols_sql = ", ".join(f"`{c}`" for c in cols)
            sql = f"ALTER TABLE `{self._name}` ADD INDEX `{index_name}` ({cols_sql});"
            result.append((index_name, sql))
        return result
    
    def destroy(self):
        """Return the ``DROP TABLE IF EXISTS`` statement for this table."""
        return f"DROP TABLE IF EXISTS `{self._name}`"
    
    def save(self, data: Dict[str, Any], foreign_key : Tuple[str, int]|None = None) -> bool:
        """
        Persist a single record and recursively save any nested child lists.

        Values for generated columns are skipped; missing required columns abort
        the operation.  After inserting the parent row, child sub-arrays are
        iterated and saved via their corresponding :class:`DBObject`, with the
        parent's primary key injected as a foreign-key value.

        Args:
            data:        Record dict keyed by column name; may contain nested lists
                         for child tables.
            foreign_key: ``(column_name, value)`` pair injected into this row to
                         satisfy a parent FK constraint.

        Returns:
            ``True`` on success, ``False`` if a required field is missing or the
            INSERT fails.
        """
        valid_columns = [col for col in self._columns if not col.generated]   
        table_data : Dict[str, Any] = {}
        for valid_column in valid_columns:
            if foreign_key is not None and valid_column.name == foreign_key[0]:
                real_data = foreign_key[1]
            else:
                real_data = data.get(valid_column.name)
            if valid_column.required and real_data is None:
                ProgressPrint.log(f"Field {valid_column.name} is required, aborting", "ERR!")
                return False
            #TODO add type control
            #print(valid_column.type, type(real_data))
            table_data[valid_column.name] = real_data
        sql_query = DBObject._create_query(self.name, table_data)
        inserted_id = self._execute_query(self.name, sql_query, table_data)
        if inserted_id is None:
            ProgressPrint.log(f"Cannot save data to {self.name} table", "ERR!")
            return False
        reference : Tuple[str, Any]|None = None
        if len(self.primary_key) == 1:
            primary_key_column = [col for col in self._columns if col.name == self.primary_key[0]][0]
            primary_key_value : int = int(data[primary_key_column.name]) if not primary_key_column.generated else inserted_id
            reference = (f"{"_".join(self._name.split("_")[1:])}_id", primary_key_value)
        else:
            return True
        relevant_reference_columns = [
            (col[0], (table_obj.primary_key[0], col[1]))
            for col in self._references 
            if col[0] in data 
            for table_obj in [cast(DBObject, DBObjects.get(col[1]))]
            if table_obj and table_obj.primary_key and len(table_obj.primary_key) == 1
        ]
        for relevant_column in relevant_reference_columns:
            relevant_column_list : List[Dict[str, Any]] = data[relevant_column[0]]
            sub_db_object = cast(DBObject, DBObjects.get(relevant_column[1][1]))
            child_fk_col = next(
                (col_name for col_name, ref_tuple in sub_db_object._fk_constraints
                 if ref_tuple[0] == self._name),
                reference[0]
            )
            child_reference = (child_fk_col, primary_key_value)
            for record in relevant_column_list:
                sub_db_object.save(record, child_reference)
        return True
    
    def save_many(self, data: List[Dict[str, Any]], foreign_key: List[Tuple[str, int]|None] = []):
        """
        Bulk-insert a list of records using ``ON DUPLICATE KEY UPDATE`` semantics.

        Skips rows with missing required fields rather than aborting the whole batch.

        Args:
            data:        List of record dicts, one per row.
            foreign_key: Parallel list of ``(column_name, value)`` FK overrides,
                         one per row (may be shorter than *data*; missing entries
                         default to ``None``).
        """
        if not data:
            return
        valid_columns = [col for col in self._columns if not col.generated]
        col_names = [col.name for col in valid_columns]
        rows: List[tuple] = []
        for i, item in enumerate(data):
            fk = foreign_key[i] if i < len(foreign_key) else None
            row = []
            skip = False
            for col in valid_columns:
                if fk is not None and col.name == fk[0]:
                    val = fk[1]
                else:
                    val = item.get(col.name)
                if col.required and val is None:
                    ProgressPrint.log(f"Field {col.name} is required, skipping row", "ERR!")
                    skip = True
                    break
                row.append(val)
            if not skip:
                rows.append(tuple(row))
        if not rows:
            return
        cols = ", ".join(f"`{c}`" for c in col_names)
        placeholders = ", ".join(["%s"] * len(col_names))
        update_part = ", ".join(f"`{c}` = VALUES(`{c}`)" for c in col_names)
        query = f"INSERT INTO `{self.name}` ({cols}) VALUES ({placeholders}) ON DUPLICATE KEY UPDATE {update_part};"
        self._db.query_many(query, rows)
        ProgressPrint.log(f"{len(rows)} rows inserted into {self.name}", "OK")
    
    @staticmethod
    def _create_query(table: str, data: Dict[str, Any]) -> str:
        """
        Build an ``INSERT … ON DUPLICATE KEY UPDATE`` statement for *table*.

        Args:
            table: Target table name.
            data:  Column → value mapping that determines the column list.

        Returns:
            Parameterised SQL string with ``%(key)s`` placeholders.
        """
        keys : List[str] = list(data.keys())
        value_placeholders = [f"%({key})s" for key in keys]
        insert_part = f"INSERT INTO `{table}` ({", ".join(keys)}) VALUES ({", ".join(value_placeholders)})"
        update_part = ", ".join([f"`{key}` = %({key})s" for key in keys])
        return f"{insert_part} ON DUPLICATE KEY UPDATE {update_part};"

    def _execute_query(self, table: str, query: str, data: Dict[str, Any]) -> int|None:
        """
        Run *query* against the database and normalise the returned row ID.

        Args:
            table: Table name (used for logging and primary-key lookup).
            query: Parameterised SQL produced by :meth:`_create_query`.
            data:  Parameter dict matching the query placeholders.

        Returns:
            The inserted row's primary-key value, or ``None`` on failure.
        """
        result = self._db.query(query, data)
        if not isinstance(result, int):
            ProgressPrint.log(f"Could not insert data to table {table}", "ERR!")
            return None
        table_obj = cast(DBObject, DBObjects.get(table))
        if result == 0:
            try:
                result = data[cast(str, table_obj.primary_key)]
            except:
                result = 0
        ProgressPrint.log(f"Data successfully inserted into {table}", "OK")
        return result

class DBObjects:
    """
    Global registry of all :class:`DBObject` instances, keyed by table name.

    Acts as a process-wide in-memory catalogue so any module can look up a
    table by name without importing or creating a new :class:`DBObject` instance.
    """

    _tables : Dict[str, DBObject] = {}

    @staticmethod
    def add(object: DBObject):
        """
        Register *object* in the global table catalogue.

        Args:
            object: The :class:`DBObject` to register under its :attr:`~DBObject.name`.
        """
        object_name = object.name
        DBObjects._tables[object_name] = object

    @staticmethod
    def get(name: str|None = None) -> Dict[str, DBObject]|DBObject:
        """
        Retrieve a single table by name, or the full catalogue when *name* is ``None``.

        Args:
            name: Table name to look up, or ``None`` to return all tables.

        Returns:
            The matching :class:`DBObject`, or the full ``{name: DBObject}`` dict.
        """
        if name is not None and name in DBObjects._tables:
            return DBObjects._tables[name]
        return DBObjects._tables

    @staticmethod
    def tables() -> List[str]:
        """Return the names of all registered tables."""
        return list(DBObjects._tables.keys())

class SchemaExplorer:
    """
    Walks a JSON-Schema document and materialises the corresponding :class:`DBObject` graph.

    A JSON Schema whose top-level type is ``"array"`` with an ``"object"`` items
    definition maps to one relational table.  Nested ``"array"`` sub-schemas
    become child tables linked by foreign keys.  Nested ``"object"`` sub-schemas
    are flattened into the parent table with a prefixed column name.

    Custom extensions understood by this walker:

    * ``_name``       – override the auto-derived table name.
    * ``_primary``    – mark a property as the primary key.
    * ``_generated``  – mark a column as AUTO_INCREMENT.
    * ``_references`` – declare an explicit FK to another entity.
    * ``_reference``  – override the FK back-reference column name in a child table.
    * ``_indexes``    – declare composite indexes as ``{index_name: [col1, …]}``.
    """

    def __init__(self, schema: JsonSchema):
        """
        Args:
            schema: The root JSON-Schema value (typically loaded from a ``.json`` file).
        """
        self._data = schema

    def validate_schema(self) -> bool:
        """
        Validate the stored schema against the internal meta-schema.

        Returns:
            ``True`` if the schema is structurally valid, ``False`` otherwise.
        """
        try:
            validate(instance=self._data, schema=META_SCHEMA)
            return True
        except ValidationError:
            ProgressPrint.log("Data schema is invalid", "ERR!")
            return False
        
    def explore(self, name: str) -> bool:
        """
        Entry point: create the :class:`DBObject` graph for the schema under *name*.

        Args:
            name: Logical name used as the root table identifier.

        Returns:
            ``True`` if the graph was built successfully, ``False`` on error.
        """
        #if not self.validate_schema():
        #    return False
        if self.create_table(self._data, name, name) is None:
            ProgressPrint.log(f"Schéma pro {ProgressPrint.BOLD}{name}{ProgressPrint.END} nebylo načteno", "WARN")
            return False
        return True

    def create_table(self, data: JsonSchema, name: str, global_prefix: str|None = None) -> str|None:
        """
        Recursively convert an ``"array"``-type schema node to a :class:`DBObject`.

        Args:
            data:          The schema node to process.
            name:          Local name for this table within its parent scope.
            global_prefix: Top-level entity prefix prepended to the table name.

        Returns:
            The final table name if created successfully, or ``None`` on invalid schema.
        """
        # Basic validation for an array-type table definition
        if not isinstance(data, dict) or data.get("type") != "array":
            return None
        
        obj_items = data.get("items")
        if not isinstance(obj_items, dict) or obj_items.get("type") != "object":
            return None
        
        obj_items_properties = cast(Dict[str, Any], obj_items.get("properties", {}))
        obj_description = cast(str, data.get("description", ""))

        # 1. Handle _name property
        table_name = f"{f'{global_prefix}_' if global_prefix is not None else ''}{name}"
        if "_name" in data and isinstance(data["_name"], str):
            table_name = data["_name"]
        db_object = DBObject(table_name, obj_description)

        # Read _indexes declarations: {"index_name": ["col1", "col2", ...], ...}
        indexes = data.get("_indexes")
        if isinstance(indexes, dict):
            for index_name, columns in indexes.items():
                if isinstance(columns, list):
                    db_object.add_index(index_name, columns)

        # Pass the current table name down so children can reference it
        if not self.property_info(table_name, obj_items_properties, db_object, global_prefix):
            return None

        DBObjects.add(db_object)
        return table_name

    def property_info(self, table_name: str, properties: Dict[str, Any], db_object: DBObject, global_prefix: str|None = None, prefix: str|None = None) -> bool:
        """
        Iterate over schema properties and populate *db_object* with columns or child tables.

        For each property:
        * ``"array"``  → recurse via :meth:`create_table` (new child table + FK).
        * ``"object"`` → flatten into the current table with a column name prefix.
        * otherwise    → create a :class:`DBColumn` and add it to *db_object*.

        Args:
            table_name:    Name of the table being built (used for FK back-references).
            properties:    The ``"properties"`` dict from the schema node.
            db_object:     The :class:`DBObject` to populate.
            global_prefix: Top-level entity prefix passed to child :meth:`create_table` calls.
            prefix:        Column name prefix accumulated from ancestor ``"object"`` nodes.

        Returns:
            ``True`` on success, ``False`` if any nested schema is malformed.
        """
        required_properties = cast(List[str]|None, properties.get("required"))
        
        for prop, prop_value in properties.items():
            if prop == "required":
                continue
            
            column_name = f"{f'{prefix}_' if prefix is not None else ''}{prop}"
            column_type = cast(str|None, prop_value.get("type"))
            
            # Handle Nested Arrays (New Tables)
            if column_type == "array":
                # Determine what the foreign key back to this table should be called
                # Default is table_name + "_id", overridden by "_reference"
                ref_column_name = prop_value.get("_reference", f"{table_name}_id")
                
                created_table = self.create_table(prop_value, prop, global_prefix)
                if created_table is None:
                    return False
                
                created_obj = cast(DBObject, DBObjects.get(created_table))
                
                # Add the foreign key to the child table pointing back to this one
                created_obj.add(DBColumn(
                    column_type="integer", 
                    name=ref_column_name, 
                    reference=(table_name, "id")
                ))
                
                # Track the relationship in the parent object
                db_object.add_reference((column_name, created_table))

            # Handle Nested Objects (Flattened into current table)
            elif column_type == "object":
                obj_props = prop_value.get("properties")
                if not isinstance(obj_props, dict):
                    return False
                else:
                    obj_props = cast(Dict[str, Any], obj_props)
                if not self.property_info(table_name, obj_props, db_object, global_prefix, prop):
                    return False

            # Handle Standard Columns
            else:
                col_max_len = cast(int|None, prop_value.get("maxLength"))
                col_pattern = cast(str|None, prop_value.get("pattern"))
                col_desc = cast(str|None, prop_value.get("description"))
                col_one_of = cast(List[Dict[str, Any]]|None, prop_value.get("oneOf"))
                col_required = True if required_properties and prop in required_properties else False
                
                primary: bool|None = None
                if "_primary" in prop_value:
                    primary = prop_value["_primary"]
                

                generated: bool|None = None
                if "_generated" in prop_value:
                    generated = prop_value["_generated"]
                    primary = True


                new_column = DBColumn(
                    column_type, column_name, col_max_len, 
                    col_pattern, col_desc, col_one_of, col_required, primary=primary, generated=generated
                )
                
                # 3. Handle _references (Foreign Keys to other main entities)
                if "_references" in prop_value:
                    ref_data = prop_value["_references"]
                    target_entity = ref_data.get("entity")
                    target_prop = ref_data.get("property", "id")

                    if target_entity:
                        new_column.reference = (target_entity, target_prop)

                db_object.add(new_column)
                
        return True