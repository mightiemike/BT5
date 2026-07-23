Looking at the extension hook plumbing, I need to trace how `sender` vs `owner` flows from the pool into each guard extension, and whether the wrong actor is being checked.

Let me verify the exact parameter binding in the extension call chain.