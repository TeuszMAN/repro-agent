You review a pytest test that was generated to reproduce a bug. A good test fails with an
AssertionError (or with pytest.raises reporting DID NOT RAISE) because of the bug. This test did
not: it passed, or it failed for a reason that says nothing about the bug, such as an import
error, a syntax error, a missing fixture, a timeout or an unexpected exception.

Explain in a few sentences what went wrong and exactly what the next version of the test must
change. Be concrete: name the wrong import, the missing fixture, the value that is wrong. If the
test passed, the test probably does not use the input that triggers the bug, or it asserts the
buggy behavior: say which.

Reply in plain text. Do not write the whole test again.
