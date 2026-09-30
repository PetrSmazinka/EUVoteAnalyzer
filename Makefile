# Default target executed when you run just 'make'
all: start

# Create virtual environment (if it doesn't exist) and install dependencies
setup:
	@echo "==> Preparing Python 3.13 virtual environment..."
	test -d venv || python3.13 -m venv venv
	./venv/bin/pip install --upgrade pip
	./venv/bin/pip install -r requirements.txt

# Start Docker containers (e.g., database, services) in background
start:
	@echo "==> Starting Docker containers..."
	docker compose up -d

# Run the main Python application with arguments inside the venv
# Arguments can be overridden from CLI: make run ARGS="--debug"
ARGS = ""
run:
	@echo "==> Running EUVoteAnalyzer application..."
	./venv/bin/python main.py $(ARGS)

# Run tests
test:
	@echo "==> Running EUVoteAnalyzer tests..."
	./venv/bin/python main.py --test

# Stop running Docker containers
stop:
	@echo "==> Stopping Docker containers..."
	docker compose down

# Clean up containers, remove persistent database volumes and delete venv
clean:
	@echo "==> Removing containers, volumes, and virtual environment..."
	docker compose down -v
	rm -rf venv

# Dump the 'ep' database from the running mariadb_eu container, gzip-compressed
# Override the output path from CLI: make dump DUMP_FILE=mydump.sql.gz
DUMP_FILE = ep_dump_$(shell date +%Y%m%d).sql.gz
dump:
	@echo "==> Dumping 'ep' database to $(DUMP_FILE)..."
	docker exec mariadb_eu mariadb-dump -u root -pzastupko --single-transaction --no-tablespaces --routines --triggers ep | gzip > $(DUMP_FILE)
	@echo "==> Done: $(DUMP_FILE)"

# Pack the project into a ZIP archive for submission (excludes cache, data, and venv)
zip:
	@echo "==> Packing project into euvoteanalyzer.zip..."
	zip -r euvoteanalyzer.zip main.py Makefile requirements.txt docker-compose.yml Dockerfile.web README.md LICENSE schema-EU.json EUVoteAnalyzer/ data-model/ web/ -x "*/__pycache__/*" "web/data/*" "venv/*"

# Restart the entire Docker environment
restart: stop start

# Declare targets that do not represent actual files on disk
.PHONY: all setup start run test stop clean publish zip restart dump