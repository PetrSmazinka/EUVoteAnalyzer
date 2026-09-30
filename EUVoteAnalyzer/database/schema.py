"""
High-level database schema management built on top of the ORM layer.

SchemaManager loads the project's JSON Schema file, uses SchemaExplorer to
build the DBObject graph, and then orchestrates table creation and deletion
in the correct topological order (respecting foreign-key dependencies).
"""

import json
from typing import List, Dict, Any, cast

from EUVoteAnalyzer.core.utils import ProgressPrint
from EUVoteAnalyzer.database.orm import DB, DBObject, DBObjects, SchemaExplorer

class SchemaManager:
    """
    Facade that drives the full database lifecycle from a JSON Schema file.

    On construction the schema file is parsed.  :meth:`load` converts it into
    the :class:`DBObject` graph; :meth:`create_tables` and :meth:`drop_tables`
    then iterate that graph in topological order to CREATE or DROP tables while
    respecting FK dependency chains.
    """

    def __init__(self, schema_path: str):
        """
        Load and parse the JSON Schema file.

        Args:
            schema_path: Filesystem path to the ``schema-*.json`` file.
        """
        self._schema = None
        self._db = DB()
        try:
            with open(schema_path, "r", encoding="utf-8") as file:
                self._schema = json.load(file)
        except FileNotFoundError:
            ProgressPrint.log(f"Schema file not found at: {schema_path}", "ERR!")
        except json.JSONDecodeError:
            ProgressPrint.log(f"File at {schema_path} is not valid JSON", "ERR!")
        except Exception as e:
            ProgressPrint.log(f"An unexpected error occurred: {e}", "ERR!")
        #TODO refactor
    
    def load(self):
        """
        Walk the schema's top-level ``"properties"`` and build the :class:`DBObject` graph.

        Each top-level property corresponds to one entity (table group).
        :class:`SchemaExplorer` handles the recursive decomposition.

        Returns:
            ``True`` on success, ``False`` if the schema was not loaded or is structurally invalid.
        """
        if self._schema is None:
            return False
        if self._schema.get("type") is None or not isinstance(self._schema.get("type"), str) or self._schema.get("type") != "object" \
           or self._schema.get("properties") is None or not isinstance(self._schema.get("properties"), dict):
            return False
        properties = cast(Dict[str, Any], self._schema.get("properties"))
        for property, property_values in properties.items():
            schema_explorer = SchemaExplorer(property_values)
            schema_explorer.explore(property)
        return True
    
    def create_tables(self):
        """
        Create all tables in topological dependency order.

        Tables are issued ``CREATE TABLE IF NOT EXISTS`` statements in an iterative
        loop that defers any table whose referenced parents have not been created yet.
        After all tables exist, self-referencing FK constraints and custom indexes are
        applied in a second pass.  Required constant seed rows (e.g. attendance types)
        are inserted last.
        """
        db_objects = cast(Dict[str, DBObject], DBObjects.get())
        total_length = len(db_objects)
        ProgressPrint.initialize("Creating DB tables")
        created_tables :List[str] = []
        while len(created_tables) < total_length:
            start_count = len(created_tables)
            for (table_key, script) in db_objects.items():
                table_name = table_key.lower()
                if table_name in created_tables:
                    continue
                script.find_primary_key()
                referenced_tables = script.find_references()
                can_create = True
                for referenced_table in referenced_tables:
                    if referenced_table.lower() not in created_tables:
                        can_create = False
                        break
                if not can_create:
                    continue
                ProgressPrint.progress(len(created_tables), total_length)
                ProgressPrint.log(f"Creating table {table_name}")
                if self._db.query(script.create()) is not False:
                    created_tables.append(table_name)
                    ProgressPrint.log(f"DB table {table_name} created", "OK")
            if len(created_tables) == start_count:
                missing = [t for t in db_objects.keys() if t.lower() not in created_tables]
                ProgressPrint.log(f"Stuck! Cannot create: {missing}", "ERR!")
                break
        if len(created_tables) == total_length:
            ProgressPrint.log("Adding self-referencing foreign keys...")
            for table_key, script in db_objects.items():
                alter_queries = script.create_alter()
                for query in alter_queries:
                    if self._db.query(query) is not False:
                        ProgressPrint.log(f"Self-reference added for {table_key.lower()}", "OK")
            ProgressPrint.log("Creating custom indexes...")
            for table_key, script in db_objects.items():
                for index_name, index_sql in script.create_indexes():
                    existing = self._db.query(
                        "SELECT INDEX_NAME FROM INFORMATION_SCHEMA.STATISTICS "
                        "WHERE TABLE_SCHEMA = DATABASE() "
                        "AND TABLE_NAME = %(t)s AND INDEX_NAME = %(i)s LIMIT 1",
                        {"t": script.name, "i": index_name}
                    )
                    if isinstance(existing, list) and not existing:
                        if self._db.query(index_sql) is not False:
                            ProgressPrint.log(f"Index {index_name} created on {script.name}", "OK")
        ProgressPrint.log("Inserting constant values")
        dochazka = cast(DBObject, DBObjects.get("dochazka"))
        for text in ("přítomnost", "částečná přítomnost", "nepřítomnost"):
            existing = self._db.query("SELECT `id` FROM `dochazka` WHERE `text` = %(t)s LIMIT 1", {"t": text})
            if not isinstance(existing, list) or not existing:
                dochazka.save({"text": text})
        ProgressPrint.finalize(total_length)

    def drop_tables(self):
        """
        Drop all existing tables in reverse dependency order.

        Only tables currently present in the database are targeted.  The iterative
        loop defers any table that still has referencing child tables until those
        children have been dropped first.
        """
        db_objects = cast(Dict[str, DBObject], DBObjects.get())
        db_objects = cast(Dict[str, DBObject], self.existing_tables(db_objects))
        total_length = len(db_objects)
        ProgressPrint.initialize("Dropping DB tables")
        deleted_tables :List[str] = []
        while len(deleted_tables) < total_length:
            start_count = len(deleted_tables)
            for (table_key, script) in db_objects.items():
                table_name = table_key.lower()
                if table_name in deleted_tables:
                    continue
                referencer_tables = script.find_referencers()
                can_drop = True
                for referencer_table in referencer_tables:
                    if referencer_table.lower() not in deleted_tables:
                        can_drop = False
                        break
                if not can_drop:
                    continue
                ProgressPrint.progress(len(deleted_tables), total_length)
                ProgressPrint.log(f"Dropping table {table_name}")
                if self._db.query(script.destroy()) is not False:
                    deleted_tables.append(table_name)
                    ProgressPrint.log(f"DB table {table_name} dropped", "OK")
            if len(deleted_tables) == start_count:
                missing = [t for t in db_objects.keys() if t.lower() not in deleted_tables]
                ProgressPrint.log(f"Stuck! Cannot drop: {missing}", "ERR!")
                break                
        ProgressPrint.finalize(total_length)

    def existing_tables(self, tables: Dict[str, DBObject]|None) -> Dict[str, DBObject]|List[str]:
        """
        Query the live database for table names and filter or return them.

        Args:
            tables: When provided, returns a filtered copy containing only entries
                    whose names exist in the database.  Pass ``None`` to get a plain
                    list of all table names currently in the schema.

        Returns:
            Filtered ``{name: DBObject}`` dict when *tables* is given, or a list of
            lowercase table name strings when *tables* is ``None``.
        """
        result = self._db.query("SHOW TABLES")
        if result != False:
            result = cast(List[Dict[str, Any]], result)
            existing_tables = [next(iter(record.values())).lower() for record in result]
            if tables is None:
                return existing_tables
            return {key: table for key, table in tables.items() if key.lower() in existing_tables}
        return []
