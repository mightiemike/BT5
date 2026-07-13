Looking at the external report, the vulnerability class is: **a privileged actor sets a cap/limit parameter without atomically verifying that existing state already satisfies the new constraint**, leaving existing positions in a broken invariant state that impairs normal user operations.

I'll now map this to the repository.