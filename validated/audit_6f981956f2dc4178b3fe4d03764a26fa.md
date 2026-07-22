### Title
`DepositAllowlistExtension` gates `owner` instead of `sender`, allowing any unpermissioned depositor to bypass the curated-pool allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the address that actually pays tokens into the pool) and only checks `owner` (the address that receives LP shares). Because `MetricOmmPoolLiquidityAdder` lets any caller freely specify an arbitrary `owner`, an address that is **not** on the allowlist can deposit into a curated pool by naming any allowlisted address as `owner`. The allowlist invariant is broken without any privileged action.

---

### Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` is declared as:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol  L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

The first positional argument — `sender`, the address that initiated the `addLiquidity` call and is paying the tokens — is unnamed and never read. Only `owner` is checked. [1](#0-0) 

The pool's `ExtensionCalling._beforeAddLiquidity` passes **both** `sender` and `owner` to the hook:

```solidity
// metric-core/contracts/ExtensionCalling.sol  (beforeAddLiquidity dispatch)
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
``` [2](#0-1) 

`MetricOmmPoolLiquidityAdder` explicitly supports adding liquidity on behalf of an arbitrary `owner`. The test `test_weighted_canAddOnBehalfOfAnotherOwner` confirms that alice (the payer) can freely set bob as `owner`:

```solidity
// metric-periphery/test/MetricOmmPoolLiquidityAdder.t.sol  L309-310
vm.prank(alice);
helper.addLiquidityWeighted(address(pool), bob, 5, w, cap, cap, ...);
``` [3](#0-2) 

When the hook fires, `msg.sender` is the pool, `owner` is bob (allowlisted), and the actual payer alice (not allowlisted) is never examined. The check passes and alice's tokens enter the pool.

---

### Impact Explanation

The deposit allowlist is the primary curation mechanism for pools that restrict who may provide liquidity (e.g., regulatory compliance, KYC-gated pools, or curated LP sets). Bypassing it means:

- **Unpermissioned funds enter the pool** — the pool admin's token-flow policy is violated.
- **Allowlisted addresses receive LP shares they did not fund** — a colluding pair (alice pays, bob receives shares) can launder alice's participation through bob's identity.
- **Pool insolvency risk** — if the allowlist is used to exclude addresses whose deposits would destabilize the pool (e.g., large whale limits), the bypass removes that protection entirely.

This is a direct policy bypass with fund-level consequences on curated pools, matching the "High direct loss or policy bypass on curated pools" impact class.

---

### Likelihood Explanation

- No privileged access is required. Any address can call `MetricOmmPoolLiquidityAdder.addLiquidityWeighted` with an arbitrary `owner`.
- The attacker only needs to know one allowlisted address (publicly readable from `allowedDepositor` mapping).
- The bypass is unconditional — it works on every curated pool using `DepositAllowlistExtension`.

---

### Recommendation

Check `sender` (the actual payer) instead of — or in addition to — `owner` in `beforeAddLiquidity`:

```solidity
function beforeAddLiquidity(address sender, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender]
        && !allowedDepositor[msg.sender][sender]   // gate the payer
        && !allowedDepositor[msg.sender][owner]) { // optionally also gate the share recipient
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

The pool admin's intent is to gate the economically relevant depositor. The `sender` is the address whose tokens enter the pool and is the correct identity to check. [1](#0-0) 

---

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` configured.
2. Pool admin calls `setAllowedToDeposit(pool, bob, true)` — bob is allowlisted; alice is not.
3. Alice calls `MetricOmmPoolLiquidityAdder.addLiquidityWeighted(pool, bob, salt, delta, ...)` with her own tokens as the payer.
4. The pool calls `_beforeAddLiquidity(sender=liquidityAdder, owner=bob, ...)`.
5. `DepositAllowlistExtension.beforeAddLiquidity` checks `allowedDepositor[pool][bob]` → `true` → no revert.
6. Alice's tokens are transferred into the pool; bob receives the LP shares.
7. Alice has successfully deposited into a pool that was supposed to exclude her, with zero privileged access required. [4](#0-3) [3](#0-2)

### Citations

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L12-14)
```text
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;
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

**File:** metric-core/contracts/ExtensionCalling.sol (L121-128)
```text
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_REMOVE_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeRemoveLiquidity, (sender, owner, salt, deltas, extensionData))
    );
```

**File:** metric-periphery/test/MetricOmmPoolLiquidityAdder.t.sol (L303-314)
```text
  function test_weighted_canAddOnBehalfOfAnotherOwner() public {
    LiquidityDelta memory w = _deltaAbovePrice(4, 100_000);
    address bob = makeAddr("bob");
    uint256 cap = 50_000;

    (int8 minBin, uint104 minPos, int8 maxBin, uint104 maxPos) = _unconstrainedCursorBounds();
    vm.prank(alice);
    helper.addLiquidityWeighted(address(pool), bob, 5, w, cap, cap, minBin, minPos, maxBin, maxPos, "");

    uint256 bobShares = stateView.positionBinShares(address(pool), bob, 5, int8(4));
    assertGt(bobShares, 0);
  }
```
