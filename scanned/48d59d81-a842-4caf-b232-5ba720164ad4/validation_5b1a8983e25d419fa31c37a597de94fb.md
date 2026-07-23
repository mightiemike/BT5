Looking at the extension guards and hook plumbing across the Metric OMM codebase, I need to trace the allowlist path, oracle guard path, and base extension path for guard misapplication.

**Key finding in `DepositAllowlistExtension.beforeAddLiquidity`:**

The hook silently drops the `sender` parameter and checks `owner` instead — but the admin-facing setter is named `setAllowedToDeposit(pool_, depositor, ...)`, where `depositor` clearly refers to the caller. Since `MetricOmmPool.addLiquidity` accepts an arbitrary `owner` address, any non-allowlisted caller can pass an allowlisted address as `owner` and the guard passes.

---

### Title
`DepositAllowlistExtension` checks position `owner` instead of transaction `sender`, allowing any unprivileged caller to bypass the configured deposit guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary
The `DepositAllowlistExtension.beforeAddLiquidity` hook ignores the `sender` argument and gates on `owner` instead. Because `MetricOmmPool.addLiquidity` accepts a free `owner` parameter, any non-allowlisted caller can deposit by naming an allowlisted address as `owner`, permanently bypassing the pool admin's configured access control.

### Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` silently drops the first parameter (`sender`) and checks the second (`owner`):

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

The admin-facing setter names its second argument `depositor`, signalling that the intent is to gate the *caller*, not the position owner:

```solidity
// L18-20
function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
```

In `MetricOmmPool.addLiquidity`, `sender = msg.sender` and `owner` is a free caller-supplied parameter:

```solidity
// metric-core/contracts/MetricOmmPool.sol L182-195
function addLiquidity(address owner, uint80 salt, ...) external nonReentrant(...) {
    ...
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
        _liquidityContext(), owner, salt, deltas, callbackData, ...
    );
```

The extension receives `(msg.sender, owner, ...)` but only checks `owner`. A non-allowlisted `msg.sender` can pass `owner = any_allowlisted_address` and the guard passes unconditionally.

Additionally, the check is inverted for the legitimate case: an allowlisted `sender` who calls `addLiquidity(owner = non_allowlisted_address)` is *blocked*, even though the sender is permitted. The guard enforces the wrong identity in both directions.

### Impact Explanation

- **Deposit allowlist fully bypassed**: Any unprivileged caller can deposit to a pool configured with `DepositAllowlistExtension` by supplying an allowlisted address as `owner`. The pool admin's configured access control is rendered ineffective.
- **Permanent token loss for the caller**: The caller pays tokens into the pool via the swap callback, but the LP shares are credited to `owner`, not `sender`. Because `removeLiquidity` enforces `msg.sender == owner`, the caller can never recover those tokens — a direct, irreversible loss of principal.
- **Unwanted LP exposure forced on the allowlisted owner**: The allowlisted address receives LP shares it did not request, increasing its exposure to pool risk (impermanent loss, stop-loss triggers, fee drag) without consent.
- **Compliance/access-control break**: Pools configured as private or KYC-gated are silently open to all callers, defeating the pool admin's configuration.

### Likelihood Explanation

- Requires no special privilege — any EOA or contract can call `addLiquidity`.
- Allowlisted addresses are discoverable on-chain from `AllowedToDepositSet` events.
- The bypass is deterministic and requires no oracle manipulation, flash loan, or race condition.
- Any pool that deploys `DepositAllowlistExtension` without `allowAllDepositors = true` is affected.

### Recommendation

Check `sender` (the actual caller) instead of `owner` (the position recipient):

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (