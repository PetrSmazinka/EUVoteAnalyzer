<?php
namespace Data;

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

if(!isset($_GET["file"])){
    http_response_code(404);
    die();
}

$analysis = $_GET["file"];
$analysis = str_replace(['../', '..\\', './', '.\\'], '', $analysis);
$pattern = '/^\/?term_[0-9]+\/(?:[a-zA-Z0-9_-]+\.json|anomalous_votes\/[0-9]+\.json|mep_comparison\/[a-zA-Z0-9_]+\.json)$/';
if (!preg_match($pattern, $analysis)) {
    return Response::error("Invalid file format or path structure");
}
$baseDir = realpath(__DIR__ . "/../data");
$filePath = $baseDir . $analysis;
if ($baseDir === false || strpos(realpath(dirname($filePath)), $baseDir) !== 0) {
    return Response::error("Access denied");
}
if (!file_exists($filePath)) {
    return Response::error("File does not exist". $filePath);
}
$content = file_get_contents(__DIR__."/../data/$analysis");
if($content === false){
    return Response::error("Could not open file");
}
return Response::ok("application/json", $content);

class Response{
    public static function ok(string $mime, mixed $content) : void {
        header("ContentType: ".$mime);
        die($content);
    }
    public static function error(mixed $content) : void {
        http_response_code(500);
        die($content);
    }
}

