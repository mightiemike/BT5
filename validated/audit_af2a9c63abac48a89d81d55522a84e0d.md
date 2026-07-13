Looking at the tieredrewards module, I need to find a "state transition without cleanup" analog — specifically, a parameter/strategy update that doesn't settle existing state first, causing downstream accounting to use the wrong value retroactively.

Let me read the governance message handler and the tier update path.