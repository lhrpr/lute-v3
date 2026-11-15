// Runs refresh.sh as a child process, so the script gets this helper's
// Full Disk Access when launchd starts it (macOS blocks background jobs from
// ~/Documents otherwise). The script path is compiled in by install.sh: the
// helper runs nothing else. A child is spawned rather than exec'd so the
// helper, not /bin/bash, stays the process macOS checks the access against.

#include <spawn.h>
#include <sys/wait.h>

extern char **environ;

int main(void) {
	char *argv[] = {"/bin/bash", SCRIPT, NULL};
	pid_t pid;
	if (posix_spawn(&pid, "/bin/bash", NULL, NULL, argv, environ) != 0) {
		return 127;
	}
	int status;
	if (waitpid(pid, &status, 0) < 0) {
		return 127;
	}
	return WIFEXITED(status) ? WEXITSTATUS(status) : 1;
}
