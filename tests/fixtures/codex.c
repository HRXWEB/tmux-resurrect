/* Native terminal process: exercise the actual hook's ancestry/argv boundary. */
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

static volatile sig_atomic_t pending = 0;
static void event(int sig) { (void)sig; pending = 1; }

static void hook(const char *directory, int count, const char *resume) {
    char path[4096], pidtext[64];
    snprintf(pidtext, sizeof(pidtext), "%ld", (long)getpid());
    pid_t child = fork();
    if (child == 0) {
        if (resume) {
            execlp("python3", "python3", getenv("CODEX_TEST_EMITTER"), resume, (char *)NULL);
        } else {
            snprintf(path, sizeof(path), "%s/%s.payload", directory, pidtext);
            int fd = open(path, O_RDONLY);
            if (fd < 0) _exit(2);
            dup2(fd, STDIN_FILENO); close(fd);
            execlp("python3", "python3", getenv("CODEX_TEST_HOOK"), (char *)NULL);
        }
        _exit(3);
    }
    int status = 0;
    waitpid(child, &status, 0);
    snprintf(path, sizeof(path), "%s/%s.done", directory, pidtext);
    FILE *done = fopen(path, "w");
    if (done) { fprintf(done, "%d %d", count, WEXITSTATUS(status)); fclose(done); }
}

int main(int argc, char **argv) {
    char path[4096];
    const char *directory = getenv("CODEX_TEST_LOG_DIR");
    if (!directory) return 1;
    signal(SIGUSR1, event);
    snprintf(path, sizeof(path), "%s/%ld.argv", directory, (long)getpid());
    FILE *file = fopen(path, "w");
    if (!file) return 1;
    for (int i = 0; i < argc; i++) fprintf(file, "%s\n", argv[i]);
    fprintf(file, "CODEX_HOME=%s\n", getenv("CODEX_HOME") ? getenv("CODEX_HOME") : "");
    fclose(file);
    int count = 0;
    if (argc > 2 && !strcmp(argv[1], "resume") && !getenv("CODEX_TEST_SKIP_RESUME_HOOK"))
        hook(directory, ++count, argv[2]);
    for (;;) {
        if (pending) { pending = 0; hook(directory, ++count, NULL); }
        usleep(10000);
    }
}
