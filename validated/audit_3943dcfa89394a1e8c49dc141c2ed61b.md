### Title
`DepositAllowlistExtension.beforeAddLiquidity` gates on `owner` instead of `sender`, allowing any unprivileged caller to bypass the deposit allowlist via `MetricOmmPoolLiquidityAdder` — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` is designed to restrict which addresses may add liquidity to a pool. Its `beforeAddLiquidity` hook silently discards the `sender` argument (the actual transaction initiator) and instead checks only the `owner` argument (the position holder). Because `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` accepts a caller-supplied `owner` with no requirement that `owner == msg.sender`, any address can bypass the allowlist by naming an already-approved owner as the position recipient while paying with its own tokens.

---

### Finding Description

**Invariant stated by the protocol:**
`DepositAllowlistExtension` is documented as "Gates `addLiquidity` by depositor address, per pool." The admin API is `setAllowedToDeposit(pool, depositor, allowed)` and the view is `isAllowedToDeposit(pool, depositor)` — both framed around the *depositor* identity.

**What the hook actually checks:**

```solidity
// DepositAllowlistExtension.sol line 32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

The first `address` parameter — `sender`, the address that called `pool.addLiquidity()` — is unnamed and **completely ignored**. Only `owner` (the position holder) is evaluated against the allowlist.

**How the pool passes these arguments:**

```solidity
// MetricOmmPool.sol line 191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`msg.sender` here is whoever called `pool.addLiquidity()` — in the adder path, that is the `MetricOmmPoolLiquidityAdder` contract. `owner` is the caller-supplied position holder.

**How the adder exposes the owner parameter without restriction:**

```solidity
// MetricOmmPoolLiquidityAdder.sol lines 56-68
function addLiquidityExactShares(
    address pool,
    address owner,   // ← caller-supplied, only checked != address(0)
    uint80 salt,
    LiquidityDelta calldata deltas,
    ...
) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);   // only: if (owner == address(0)) revert
    ...
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, ...);
}
```

`_validateOwner` only rejects `address(0)`. There is no check that `msg.sender == owner`.

**Full attack path:**

1. Pool is deployed with `DepositAllowlistExtension` configured in `BEFORE_ADD_LIQUIDITY_ORDER`.
2. Pool admin allowlists Alice (`allowedDepositor[pool][alice] = true`). Bob is **not** on the allowlist.
3. Bob calls `addLiquidityExactShares(pool, alice, salt, deltas, max0, max1, extensionData)`.
4. The adder calls `pool.addLiquidity(alice, salt, deltas, abi.encode(KIND_PAY), extensionData)`.
5. The pool calls `_beforeAddLiquidity(adder, alice, ...)`.
6. `ExtensionCalling` encodes and dispatches `beforeAddLiquidity(adder, alice, ...)` to the extension.
7. The extension checks `allowedDepositor[pool][alice]` → **passes**.
8. Bob's tokens are pulled in the callback (`payer = msg.sender = bob`), minted into Alice's position.

Bob — an address that was never approved — has successfully deposited into the pool. The same path works with `addLiquidityWeighted(pool, alice, ...)`: the probe call also passes the allowlist check using Alice's address, and the subsequent paying call does too.

---

### Impact Explanation

The deposit allowlist is the primary access-control mechanism for restricted pools (e.g., KYC-gated, regulatory-compliant, or curated LP pools). Any unprivileged address can bypass it entirely by routing through `MetricOmmPoolLiquidityAdder` and naming any already-approved address as `owner`. The pool receives liquidity from an unauthorized depositor, breaking the core invariant the extension was deployed to enforce. This falls under **broken core pool functionality** and **admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path**.

---

### Likelihood Explanation

- `MetricOmmPoolLiquidityAdder` is a public, permissionless contract.
- The only precondition is knowing one allowlisted address — trivially discoverable on-chain from `AllowedToDepositSet` events.
- No privileged role, special token, or flash-loan is required.
- The bypass works on every pool that uses `DepositAllowlistExtension` with a non-`allowAll` configuration.

---

### Recommendation

In `DepositAllowlistExtension.beforeAddLiquidity`, gate on `sender` (the actual transaction initiator) rather than `owner` (the position holder):

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

Note that `sender` in the direct-pool path is `msg.sender` of `pool.addLiquidity()`, which equals the adder contract when the adder is used. If per-end-user gating through the adder is also required, the adder must forward the original `msg.sender` as an additional argument or the allowlist must be checked at the adder level before the pool call.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.35;

// Assume pool is deployed with DepositAllowlistExtension in beforeAddLiquidity slot.
// Alice is allowlisted; Bob is not.

contract DepositAllowlistBypass {
    MetricOmmPoolLiquidityAdder adder;
    address pool;
    address alice; // allowlisted
    address bob;   // NOT allowlisted

    function exploit() external {
        // Bob calls as msg.sender, but supplies alice as owner.
        // _validateOwner only checks alice != address(0) — passes.
        // beforeAddLiquidity receives (adder, alice, ...) and checks allowedDepositor[pool][alice] → true.
        // Bob's tokens are pulled in the callback; Alice's position is credited.
        vm.prank(bob);
        adder.addLiquidityExactShares(
            pool,
            alice,   // ← authorized owner used to pass the allowlist
            0,
            deltas,
            type(uint256).max,
            type(uint256).max,
            ""
        );
        // Bob has deposited into a pool he was never approved for.
    }
}
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-68)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L188-196)
```text
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
