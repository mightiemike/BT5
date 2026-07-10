Looking at the repository structure, I need to find a cross-module desynchronization where a function intended to work under a restricted state internally calls another function that is blocked by that same restriction.

Let me read the key files more carefully.