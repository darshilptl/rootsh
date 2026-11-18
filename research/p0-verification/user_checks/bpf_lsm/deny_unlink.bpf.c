// P0 feasibility probe for K4 (BPF-LSM): deny unlink of any file named
// exactly "rootsh_p0_deny". Proves an LSM program loads, attaches, and
// enforces on this kernel. The real K4 keys decisions on the agent's cgroup
// and an intent-scope map instead of a fixed name.
#include "vmlinux.h"
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_tracing.h>

#define EPERM 1

char LICENSE[] SEC("license") = "GPL";

static const char target[] = "rootsh_p0_deny";

// The function name becomes the bpffs pin name; it must not match `target`,
// or the program blocks its own unpinning.
SEC("lsm/inode_unlink")
int BPF_PROG(p0_unlink_guard, struct inode *dir, struct dentry *dentry, int ret)
{
	// One byte longer than target: a longer name is truncated by the read,
	// so the returned length (including NUL) separates exact matches from
	// names that merely start with target.
	char name[sizeof(target) + 1];

	if (ret != 0)
		return ret;
	if (bpf_probe_read_kernel_str(name, sizeof(name), dentry->d_name.name) != sizeof(target))
		return 0;
	for (int i = 0; i < sizeof(target); i++)
		if (name[i] != target[i])
			return 0;
	return -EPERM;
}
