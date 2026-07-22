### Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Any Caller to Bypass the Deposit Guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently drops the `sender` argument and gates on `owner` (the position recipient) instead. Because `addLiquidity` lets `msg.sender` differ from `owner`, any address not on the allowlist can deposit into a restricted pool by nominating an allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` accepts an arbitrary `owner` address and enforces no relationship between `msg.sender` and `owner`: [1](#0-0) 

The pool then fires `_beforeAddLiquidity(msg.sender, owner, ...)`, forwarding both the actual caller (`sender`) and the position recipient (`owner`) to every configured extension: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `(sender, owner, …)` but discards `sender` (first parameter is unnamed) and checks only `owner`: [3](#0-2) 

The contract's own NatSpec says it "Gates `addLiquidity` by depositor address" and the mapping is named `allowedDepositor`, both indicating the intent is to restrict the **caller**, not the position recipient. The implementation checks the wrong address.

Contrast with `removeLiquidity`, which enforces `msg.sender == owner` before calling the hook, making `sender` and `owner` identical there — the asymmetry is only exploitable on the deposit path. [4](#0-3) 

**Attack path:**

1. Pool admin deploys a pool with `DepositAllowlistExtension` and allowlists only `trustedLP`.
2. Attacker (not allowlisted) calls `pool.addLiquidity(trustedLP, salt, deltas, callbackData, extensionData)`.
3. `beforeAddLiquidity` checks `allowedDepositor[pool][trustedLP]` → passes.
4. Attacker's callback pays the tokens; the LP position is minted to `trustedLP`.
5. `trustedLP` now holds an LP position it did not request; the deposit allowlist is fully bypassed.

---

### Impact Explanation

The deposit allowlist is the primary access-control mechanism for restricted pools. Its bypass means:

- Any unprivileged address can add liquidity to a pool the admin intended to keep private or permissioned.
- The allowlisted address receives an unsolicited LP position. If that address is a contract without `removeLiquidity` logic, the tokens are effectively locked in the pool under a position the contract cannot redeem.
- The pool's liquidity distribution is altered without the admin's consent, which can shift fee income away from legitimate LPs and change the pool's bin-level exposure.

This matches the **Allowlist path** pivot ("deposit/swap allowlist checks must cover the exact actor/action intended and cannot be bypassed through … owner/salt separation") and the **Admin-boundary break** impact gate ("factory/oracle role checks are bypassed by an unprivileged path").

---

### Likelihood Explanation

The exploit requires only a public call to `addLiquidity` with a known allowlisted address as `owner`. No special privileges, flash loans, or oracle manipulation are needed. Any observer of on-chain allowlist events (`AllowedToDepositSet`) can identify a valid `owner` and execute the bypass immediately.

---

### Recommendation

Replace the `owner` check with a `sender` check in `beforeAddLiquidity`:

```solidity
// DepositAllowlistExtension.sol
function beforeAddLiquidity(address sender, address /*owner*/, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

If the intended semantics are to restrict the position recipient rather than the caller, rename the mapping and NatSpec accordingly and document that any caller can deposit on behalf of an allowlisted owner — but the current naming and documentation make clear the intent is to gate the caller.

---

### Proof of Concept

```solidity
// Assume pool has DepositAllowlistExtension configured.
// Only `trustedLP` is allowlisted; `attacker` is not.

vm.prank(attacker);
// owner = trustedLP (allowlisted) → check passes; attacker pays via callback
pool.addLiquidity(
    trustedLP,          // owner — checked by extension, passes
    0,                  // salt
    deltas,
    callbackData,       // attacker's contract pays tokens here
    extensionData
);

// trustedLP now holds shares it never requested.
// Deposit allowlist is bypassed; attacker added liquidity to a restricted pool.
``` [3](#0-2)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L182-196)
```text
  function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L199-212)
```text
  function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas, bytes calldata extensionData)
    external
    nonReentrant(PoolActions.REMOVE_LIQUIDITY)
    returns (uint256 amount0Removed, uint256 amount1Removed)
  {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    if (msg.sender != owner) revert NotPositionOwner();
    _beforeRemoveLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Removed, amount1Removed) = LiquidityLib.removeLiquidity(
      _liquidityContext(), owner, salt, deltas, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterRemoveLiquidity(msg.sender, owner, salt, deltas, amount0Removed, amount1Removed, extensionData);
  }
```

**File:** metric-core/contracts/ExtensionCalling.sol (L88-99)
```text
  function _beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
  }
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
