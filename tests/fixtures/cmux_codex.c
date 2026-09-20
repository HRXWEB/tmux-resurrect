/* A terminal process that records argv without contacting an AI service. */
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

int main(int argc, char **argv) {
    char path[4096];
    const char *directory = getenv("CMUX_TEST_LOG_DIR");
    if (!directory) return 1;
    snprintf(path, sizeof(path), "%s/%ld", directory, (long)getpid());
    FILE *file = fopen(path, "w");
    if (!file) return 1;
    for (int i = 0; i < argc; i++) fprintf(file, "%s\n", argv[i]);
    const char *home = getenv("CODEX_HOME");
    fprintf(file, "CODEX_HOME=%s\n", home ? home : "");
    fclose(file);
    for (;;) pause();
}
