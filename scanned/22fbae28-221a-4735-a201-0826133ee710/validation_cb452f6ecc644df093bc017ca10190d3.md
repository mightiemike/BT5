### Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Any Unprivileged Actor to Bypass the Deposit Guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` is documented as gating `addLiquidity` **by depositor address**. However, its `beforeAddLiquidity` hook validates the caller-supplied `owner` argument (the position recipient) rather than `sender` (the actual `msg.sender` of the pool call, i.e., the entity providing tokens). Because `MetricOmmPool.addLiquidity` accepts an arbitrary `owner` with no requirement that `owner == msg.sender`, any non-allowlisted actor can bypass the guard by passing any allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` accepts a free `owner` parameter and passes both `msg.sender` (as `sender`) and `owner` to the extension hook: [1](#0-0) 

The pool imposes **no constraint** that `owner == msg.sender` in `addLiquidity`. The only ownership check in the pool is in `removeLiquidity`: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` ignores the first argument (`sender`) and validates only `owner`: [3](#0-2) 

The NatDoc explicitly states the contract "Gates `addLiquidity` by **depositor** address", yet the depositor (`sender`) is silently discarded. By contrast, `SwapAllowlistExtension` correctly validates `sender` (the actual swapper): [4](#0-3) 

The asymmetry confirms the `DepositAllowlistExtension` implementation diverges from both its own documentation and the pattern established by its sibling extension.

---

### Impact Explanation

**Admin-boundary break / broken core pool functionality.**

A pool admin deploys `DepositAllowlistExtension` to restrict which addresses may provide liquidity (e.g., for regulatory KYC compliance or to limit LP composition). The guard is entirely ineffective:

1. Attacker (non-allowlisted) identifies any allowlisted address `A`.
2. Attacker calls `pool.addLiquidity(owner = A, ...)`, supplying their own tokens via the swap callback.
3. `beforeAddLiquidity` receives `sender = attacker`, `owner = A`; it checks only `A` (allowlisted) → passes.
4. Attacker's tokens enter the pool; the LP position is credited to `A`.
5. `A` (colluding or socially-engineered) calls `removeLiquidity` and returns the proceeds to the attacker.

The net result: non-allowlisted capital freely enters and exits the pool. The pool admin's access-control invariant — that only approved depositors can provide liquidity — is completely broken. In a regulatory or permissioned-LP context this constitutes a material admin-boundary break with direct fund-flow consequences (unapproved capital in pool, unapproved LP fees earned).

---

### Likelihood Explanation

**Medium.** Allowlisted addresses are visible on-chain (emitted in `AllowedToDepositSet` events). Any actor who can read chain state can identify a valid `owner` to pass. The only coordination requirement is that the allowlisted address cooperates to return funds; in a griefing variant no cooperation is needed (attacker simply forces an unwanted position onto the allowlisted address). The attack requires no special privilege, no flash loan, and no exotic token behavior.

---

### Recommendation

Replace the `owner` check with a `sender` check, mirroring `SwapAllowlistExtension`:

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

If the intended semantics are to gate by position owner (not depositor), the NatDoc and the mapping key name (`allowedDepositor`) must be corrected and the design rationale documented, because the current mismatch between documentation and code is itself a correctness hazard.

---

### Proof of Concept

```
Setup:
  pool configured with DepositAllowlistExtension
  allowedDepositor[pool][alice] = true   // alice is KYC'd
  bob is NOT allowlisted

Attack:
  vm.prank(bob);
  pool.addLiquidity(
      owner        = alice,   // allowlisted → guard passes
      salt         = 0,
      deltas       = <valid bins>,
      callbackData = "",      // bob's callback pays tokens
      extensionData= ""
  );
  // Extension checks alice (allowed) → no revert
  // Bob's tokens enter pool; position credited to alice

  vm.prank(alice);            // alice (colluding) removes
  pool.removeLiquidity(alice, 0, deltas, "");
  // alice returns proceeds to bob off-chain

Result: bob (non-allowlisted) has effectively provided liquidity and
        earned LP fees, bypassing the deposit allowlist entirely.
``` [3](#0-2) [5](#0-4)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
  }
```
