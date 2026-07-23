### Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks Wrong Actor (`owner` Instead of `sender`), Allowing Any Caller to Bypass the Deposit Allowlist — (`File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary

`DepositAllowlistExtension` is the production extension that gates `addLiquidity` by depositor address. Its `beforeAddLiquidity` hook silently discards the `sender` parameter (the actual caller who pays tokens) and instead validates `owner` (the position recipient). Because `MetricOmmPool.addLiquidity` explicitly supports an operator pattern where `msg.sender ≠ owner`, any unprivileged address can bypass the allowlist by supplying an already-allowed address as `owner`, paying the tokens themselves, and forcing that address to receive an unwanted LP position.

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct addresses to the extension hook:

- `sender` = `msg.sender` of `addLiquidity` — the actual caller who will pay tokens via the `IMetricOmmModifyLiquidityCallback`
- `owner` = the position owner supplied by the caller — who receives the LP shares [1](#0-0) 

The pool passes both to the extension: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `(address sender, address owner, ...)` but discards `sender` (unnamed `address`) and checks only `owner`: [3](#0-2) 

The contract's own NatSpec says it "Gates `addLiquidity` by depositor address" — the depositor is the entity that pays, i.e., `sender`, not `owner`. The sibling `SwapAllowlistExtension` correctly checks `sender` (the swap caller): [4](#0-3) 

`removeLiquidity` enforces `msg.sender == owner`, so the operator pattern is one-directional: anyone can add liquidity for any `owner`, but only `owner` can remove it: [5](#0-4) 

### Impact Explanation

The deposit allowlist is completely ineffective. Any address not on the allowlist can deposit into a restricted pool by:

1. Picking any address `alice` that is on the allowlist.
2. Calling `pool.addLiquidity(owner=alice, salt, deltas, callbackData, extensionData)`.
3. The extension checks `allowedDepositor[pool][alice]` → `true` → passes.
4. The pool issues the `metricOmmModifyLiquidityCallback` to `msg.sender` (the attacker), who pays the tokens.
5. `alice` receives the LP position she never requested.

The pool admin's access control intent (e.g., KYC/AML gating, restricting liquidity providers to trusted parties) is entirely defeated. Unauthorized capital enters the pool, and the allowed address accumulates unwanted positions it must actively remove.

### Likelihood Explanation

- No special privilege is required — any EOA or contract can call `addLiquidity`.
- The operator pattern (`msg.sender ≠ owner`) is explicitly supported and documented.
- The bypass requires only knowing one allowed address, which is publicly readable via `allowedDepositor(pool, addr)` or `AllowedToDepositSet` events.
- The `MetricOmmPoolLiquidityAdder` periphery contract itself calls `addLiquidity` with `owner = msg.sender` of the outer call, confirming the operator pattern is a live, intended flow. [6](#0-5) 

### Recommendation

Replace the discarded first parameter with a named `sender` and check it instead of (or in addition to) `owner`, mirroring `SwapAllowlistExtension`:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
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

If the intent is to gate both the caller and the position owner, both should be checked.

### Proof of Concept

```solidity
// Setup: pool with DepositAllowlistExtension; only `alice` is allowed.
extension.setAllowedToDeposit(address(pool), alice, true);

// Bob (not on allowlist) constructs a deposit on behalf of alice.
// Bob's contract implements IMetricOmmModifyLiquidityCallback and pays tokens.
vm.prank(bob);
pool.addLiquidity(
    alice,          // owner — alice is allowed, check passes
    salt,
    deltas,
    callbackData,   // bob's contract pays here
    extensionData
);

// Result: bob bypassed the allowlist; alice has an unwanted LP position.
// extension.isAllowedToDeposit(pool, bob) == false, yet the deposit succeeded.
```

The existing unit test `test_revertsWhenDepositorNotAllowed` passes `address(0)` as `sender` and `depositor` as `owner`, so it only validates the `owner` path and never exercises the bypass: [7](#0-6)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
```

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L139-147)
```text
    try IMetricOmmPoolActions(pool)
      .addLiquidity(msg.sender, salt, weightDeltas, abi.encode(KIND_PROBE), extensionData) returns (
      uint256, uint256
    ) {
      revert WeightedProbeInconclusive();
    } catch (bytes memory reason) {
      (uint256 need0, uint256 need1) = _decodeLiquidityProbeOrBubble(reason);
      LiquidityDelta memory scaled = _scaleWeightsToShares(weightDeltas, maxAmountToken0, maxAmountToken1, need0, need1);
      return _addLiquidity(pool, msg.sender, salt, scaled, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
```

**File:** metric-periphery/test/extensions/DepositAllowlistSubExtension.t.sol (L27-31)
```text
  function test_revertsWhenDepositorNotAllowed() public {
    vm.prank(address(pool));
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToDeposit.selector);
    LiquidityDelta memory emptyDelta = LiquidityDelta({binIdxs: new int256[](0), shares: new uint256[](0)});
    extension.beforeAddLiquidity(address(0), depositor, 0, emptyDelta, "");
```
