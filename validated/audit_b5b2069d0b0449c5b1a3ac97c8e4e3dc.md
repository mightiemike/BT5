Looking at the external report's vulnerability class: **the protocol itself constructs an identifier using a character that is forbidden by its own validation, causing all operations on that identifier to fail**.

I need to find whether this repo has a case where the protocol constructs an identifier that its own validation then rejects.