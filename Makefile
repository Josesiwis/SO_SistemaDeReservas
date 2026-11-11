CXX = g++
CXXFLAGS = -std=c++17 -Wall -Wextra -Wpedantic -pthread
TARGET = reservas_pasajes
SRCS = reservas_pasajes.cpp
HEADERS = reservas_pasajes.hpp

all: $(TARGET)

$(TARGET): $(SRCS) $(HEADERS)
	$(CXX) $(CXXFLAGS) $(SRCS) -o $(TARGET)

run: $(TARGET)
	./$(TARGET)

test: $(TARGET)
	python3 -m unittest discover -s tests

clean:
	rm -f $(TARGET)
	find . -type d -name "__pycache__" -exec rm -rf {} +
	rm -f *.sqlite3 *.sqlite3-shm *.sqlite3-wal

.PHONY: all run test clean
