/*
 * replay.c — Replays captured frames to verify the serial connection
 *
 * Sends the exact captured frames in a loop so the company software
 * can confirm it receives and parses them correctly.
 *
 * Compile: gcc -o replay replay.c
 * Run:     ./replay
 */

#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <termios.h>
#include <signal.h>
#include <time.h>

#define SERIAL_PORT "/dev/pts/8"
#define BAUD_RATE    B115200
#define PACKET_SIZE  45

static volatile int running = 1;
static void handle_sigint(int s) { (void)s; running = 0; }

/* Captured frames — use Capture 2 (stable, repeating pattern) */
static const uint8_t frames[][PACKET_SIZE] = {
    {
        0x80, 0xc0,
        0x08, 0x02, 0x08, 0x02, 0xf9, 0x01, 0xe9, 0x01, 0xf7, 0x01,
        0xc1,
        0xf9, 0x01, 0x08, 0x02, 0xf9, 0x01, 0xe9, 0x01, 0xf7, 0x01,
        0xc2,
        0xe9, 0x01, 0x08, 0x02, 0xf9, 0x01, 0xe9, 0x01, 0xf7, 0x01,
        0xc3,
        0xf7, 0x01, 0x08, 0x02, 0xf9, 0x01, 0xe9, 0x01, 0xf7, 0x01
    },
};
#define NFRAMES (int)(sizeof(frames) / sizeof(frames[0]))

static int open_serial(const char *port) {
    int fd = open(port, O_RDWR | O_NOCTTY | O_NONBLOCK);
    if (fd < 0) { perror("open serial"); return -1; }
    struct termios t;
    tcgetattr(fd, &t);
    cfsetispeed(&t, BAUD_RATE);
    cfsetospeed(&t, BAUD_RATE);
    t.c_cflag = (t.c_cflag & ~CSIZE) | CS8;
    t.c_cflag &= ~PARENB;
    t.c_cflag &= ~CSTOPB;
    t.c_cflag &= ~CRTSCTS;
    t.c_cflag |= CREAD | CLOCAL;
    t.c_lflag = 0;
    t.c_iflag = 0;
    t.c_oflag = 0;
    tcsetattr(fd, TCSANOW, &t);
    return fd;
}

int main(void) {
    signal(SIGINT, handle_sigint);

    printf("Opening serial port %s ...\n", SERIAL_PORT);
    int fd = open_serial(SERIAL_PORT);
    if (fd < 0) {
        printf("Make sure socat is running first.\n");
        return 1;
    }
    printf("Sending captured frames in loop (Ctrl+C to stop)...\n\n");
    printf("  %-8s  %-8s\n", "Packets", "Frame");
    printf("  %s\n", "-------------------");

    long sent = 0;
    struct timespec ts = {0, 10000000}; /* 10ms = 100 Hz */

    while (running) {
        for (int i = 0; i < NFRAMES && running; i++) {
            if (write(fd, frames[i], PACKET_SIZE) < 0) {
                perror("write");
                running = 0;
                break;
            }
            sent++;
            printf("  %-8ld  frame[%d]\r", sent, i);
            fflush(stdout);
            nanosleep(&ts, NULL);
        }
    }

    close(fd);
    printf("\n\nStopped. Total packets sent: %ld\n", sent);
    return 0;
}
