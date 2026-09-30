<?php
namespace API;
require_once __DIR__."/settings.php";

class DB {
    protected \PDO $connection;
    private static $instances = [];

    protected function __construct() {
        
        $dsn = "mysql:host=".DB_SERVER.";dbname=".DB_DATABASE.";charset=utf8mb4";
        $options = [
            \PDO::ATTR_ERRMODE            => \PDO::ERRMODE_EXCEPTION,
            \PDO::ATTR_DEFAULT_FETCH_MODE => \PDO::FETCH_ASSOC,
            \PDO::ATTR_EMULATE_PREPARES   => false,
        ];

        try {
            $this->connection = new \PDO($dsn, DB_USERNAME, DB_PASSWORD, $options);
        } catch (\PDOException $e) {
            throw new \PDOException($e->getMessage(), (int)$e->getCode());
        }
    }

    protected function __clone() { }
    
    public function __wakeup(){
        throw new \Exception("Cannot unserialize a singleton.");
    }

    public static function getInstance(): DB {
        $cls = static::class;
        if (!isset(self::$instances[$cls])) {
            self::$instances[$cls] = new static();
        }
        return self::$instances[$cls];
    }

    public function select(string $query, array $params = []): array {
        try {
            $stmt = $this->connection->prepare($query);
            $stmt->execute($params);
            return $stmt->fetchAll();
        }
        catch (\PDOException $e) {
            if(DEBUG){
                throw $e;
            }
            else{
                throw new \Exception("DB error");
            }
        }
    }

    public function query(string $query, array $params = []): bool {
        try {
            $stmt = $this->connection->prepare($query);
            return $stmt->execute($params);
        }
        catch (\PDOException $e) {
            if ($this->connection->inTransaction()) {
                $this->rollback();
            }
            if(DEBUG){
                throw $e;
            }
            else{
                throw new \Exception("DB error");
            }
        }
    }

    public function begin() : bool {
        return $this->connection->beginTransaction();
    }

    public function commit() : bool {
        return $this->connection->commit();
    }

    public function rollback() : bool {
        return $this->connection->rollBack();
    }

    public function error() : ?string {
        $error = $this->connection->errorInfo();
        return $error[2] ?? null;
    }
}