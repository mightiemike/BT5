### Title
`DepositAllowlistExtension` Gates the Wrong Identity — `owner` Checked Instead of `sender`, Allowlist Bypassed by Any Caller - (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` receives the hook arguments `(sender, owner, salt, deltas, extensionData)` but silently discards `sender` (position 1) and gates on `owner` (position 2). Because `owner` is caller-supplied and completely independent of who pays the tokens, any unprivileged address can bypass the deposit allowlist by setting `owner` to any already-allowlisted address.

---

### Finding Description

`ExtensionCalling._beforeAddLiquidity` encodes the hook call as:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeAddLiquidity,
    (sender, owner, salt, deltas, extensionData)
)
``` [1](#0-0) 

`sender` (position 1) is the address that called `addLiquidity` and is paying the tokens. `owner` (position 2) is the address that will receive the LP shares — a free parameter chosen by the caller.

The extension's guard reads:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [2](#0-1) 

`sender` is unnamed and discarded. The check `allowedDepositor[msg.sender][owner]` tests whether the LP-share recipient is allowlisted, not whether the token payer is allowlisted.

This is the direct analog of the external report's bug class: a multi-argument hook call where the caller reads the wrong positional value — position 2 (`owner`) instead of position 1 (`sender`) — causing the guard to be applied to the wrong identity.

---

### Impact Explanation

Any unprivileged address can call `pool.addLiquidity(owner=<allowlisted_address>, ...)` directly. The extension sees `owner` = an allowlisted address, the check passes, and the deposit proceeds. The non-allowlisted caller pays the tokens; the allowlisted address receives the LP shares.

Consequences:
- The deposit allowlist — the pool admin's primary access-control mechanism for liquidity — is fully bypassed for any caller who knows one allowlisted address.
- A non-allowlisted actor can inject liquidity into a restricted pool, disrupting the intended liquidity distribution and pool composition.
- The allowlisted address receives LP shares it did not request; while it can remove them, the pool's state has already been altered.
- The invariant `allowedDepositor[pool][depositor] == true` for every depositor is broken: the depositor (token payer = `sender`) is never checked.

---

### Likelihood Explanation

- Trigger requires no special role, no privileged setup, and no non-standard token behavior.
-

### Citations

**File:** metric-core/contracts/ExtensionCalling.sol (L95-98)
```text
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
