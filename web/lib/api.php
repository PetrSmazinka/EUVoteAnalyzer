<?php
namespace API;

if (isset($_SERVER['HTTP_ORIGIN'])) {
    header("Access-Control-Allow-Origin: " . $_SERVER['HTTP_ORIGIN']);
} else {
    header("Access-Control-Allow-Origin: *");
}

header("Access-Control-Allow-Methods: GET, POST, OPTIONS, PUT, DELETE");
header("Access-Control-Allow-Headers: Content-Type, Authorization, X-Requested-With, Accept, Origin, Access-Control-Allow-Headers");
header("Access-Control-Allow-Credentials: true");
header("Cache-Control: no-cache, must-revalidate");

if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') {
    if (isset($_SERVER['HTTP_ACCESS_CONTROL_REQUEST_METHOD'])) {
        header("Access-Control-Allow-Methods: GET, POST, OPTIONS, PUT, DELETE");
    }
    if (isset($_SERVER['HTTP_ACCESS_CONTROL_REQUEST_HEADERS'])) {
        header("Access-Control-Allow-Headers: {$_SERVER['HTTP_ACCESS_CONTROL_REQUEST_HEADERS']}");
    }
    http_response_code(200);
    exit;
}

require_once __DIR__."/lib.php";

$files=null;
if(!empty($_POST) && isset($_POST['request'])){
    $request = $_POST['request'];
    $data = $_POST;
    if (!empty($_FILES)) {
        $files = $_FILES; 
    }
    unset($data['request']);
}
if(!empty($_GET) && isset($_GET['request'])){
    $request = $_GET['request'];
    $data = $_GET;
    if (!empty($_FILES)) {
        $files = $_FILES; 
    }
    unset($data['request']);
}
else{
    $input = file_get_contents('php://input');
    $json = json_decode($input, true);
    if(!$json || !isset($json["request"])){
        die(json_encode(["status" => "error", "message" => "No request specified"]));
    }
    $request = $json["request"];
    unset($json["request"]);
    $data = $json;
}
die(json_encode(API::handle($request, $data, $files)));

class API{
    private static $endpoints = [];
    public static function register(Endpoint $endpoint) : void {
        API::$endpoints[$endpoint->getName()] = $endpoint;
    }
    public static function handle(string $name, array|null $data, array|null $files) : array {
        if (!isset(API::$endpoints[$name])) {
            return ["status" => "error"];
        }
        return self::$endpoints[$name]->call($data, $files);
    }
}

class Endpoint{
    public function __construct(
        public string $name,
        public mixed $target,
        public array $mandatory = []
    ) {
        if (!is_callable($target)) {
            throw new \InvalidArgumentException("Target must be callable.");
        }
        API::register($this);
    }
    public function call(array|null $data, array|null $files) : array {
        foreach($this->mandatory as $mandatory){
            $inData = ($data !== null && isset($data[$mandatory]));
            $inFiles = ($files !== null && isset($files[$mandatory]));
            if(!$inData && !$inFiles){
                return ["status" => "error"];
            }
        }
        return ($this->target)($data, $files);
    }
    public function getName() : string {
        return $this->name;
    }
}

class Response{
    public static function ok(mixed $data = null) : array {
        if($data == null){
            return ["status" => "ok"];
        }
        return ["status" => "ok", "data" => $data];
    }

    public static function error(mixed $data) : array {
        if(DEBUG){
            return ["status" => "error", "message" => $data];
        }
        return ["status" => "error"];
    }

    public static function file(string $mime, string $content) : void {
        header("ContentType: ".$mime);
        die($content);
    }

    public static function fileError(string $content) : void {
        http_response_code(500);
        die($content);
    }
}
