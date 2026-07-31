CC       = gcc
CFLAGS   = -Wall -Wextra -O2
GTK_FLAGS = $(shell pkg-config --cflags --libs gtk+-3.0)

all: reader bridge gui replay basic_interface

reader: reader.c
	$(CC) $(CFLAGS) -o reader reader.c -lpthread -lm

bridge: bridge.c
	$(CC) $(CFLAGS) -o bridge bridge.c -lm

gui: gui.c
	$(CC) $(CFLAGS) -o gui gui.c $(GTK_FLAGS) -lpthread -lm

replay: replay.c
	$(CC) $(CFLAGS) -o replay replay.c

basic_interface: basic_interface.c
	$(CC) $(CFLAGS) -o basic_interface basic_interface.c $(GTK_FLAGS) -lpthread -lm

clean:
	rm -f reader bridge gui replay basic_interface
