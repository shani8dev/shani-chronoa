"""Mutation set for the architecture guard on the seccomp table.

The syscall numbers in `sandbox/seccomp.py` are literal x86_64 numbers.
`supported_architecture()` refuses to install them anywhere else, because on
aarch64 they would deny "whatever syscall happens to occupy index 165 there".

`unavailable_reason()` covers it and `apply()` raises on that reason, so the guard
is wired into both the pre-flight and the install - and it had no test at all
until `TestTheArchitectureGuard`. A guard nobody tests does not exist: a change
that dropped the check, or inverted it, would install the table on any machine,
and the symptom is in this file's own docstring - an inverted arch guard installs
cleanly, reports `Seccomp: 2`, and answers ENOSYS to every syscall including
write(2), so the process dies before it prints and every "did the filter install"
assertion still passes.
"""

TEST_FILE = "tests/test_sandbox_seccomp.py"

_SECCOMP = "usr/lib/shani-chronoa/shani_chronoa/sandbox/seccomp.py"

MUTATIONS = [
    {
        "file": _SECCOMP,
        "old": '    return architecture() == "x86_64"\n',
        "new": "    return True\n",
        # The guard removed. Every architecture is accepted, so an aarch64 box
        # would install x86_64 numbers and deny whatever holds those indices.
        "label": "the architecture guard always accepts",
        "expect": ["test_a_foreign_architecture_is_refused_with_the_number_named",
                   "test_installing_on_a_foreign_architecture_raises_rather_than_installing",
                   "test_the_numbers_are_x86_64_and_the_guard_names_the_one_it_cannot_hold"],
    },
    {
        "file": _SECCOMP,
        "old": '    return architecture() == "x86_64"\n',
        "new": '    return architecture() != "x86_64"\n',
        # The guard inverted - the failure this file's docstring describes, in
        # which the filter installs cleanly and then denies everything.
        "label": "the guard is inverted",
        "expect": ["test_this_machines_table_matches_this_machines_architecture",
                   "test_the_architecture_is_not_the_reason_on_a_machine_it_fits",
                   "test_installing_on_a_foreign_architecture_raises_rather_than_installing"],
    },
    {
        "file": _SECCOMP,
        "old": "    if not supported_architecture():\n",
        "new": "    if False:\n",
        # The pre-flight drops the check while `apply()` keeps its own, so the
        # filter would still refuse. The preflight is what a caller shows a
        # person; losing it means the refusal arrives as an install failure with
        # an errno instead of as a policy answer with a readable message.
        "label": "the preflight drops the architecture check",
        "expect": ["test_a_foreign_architecture_is_refused_with_the_number_named",
                   "test_installing_on_a_foreign_architecture_raises_rather_than_installing"],
    },
]
