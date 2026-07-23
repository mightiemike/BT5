### Title
SwapAllowlistExtension Checks Direct Caller (Router) Instead of Actual Swapper, Enabling Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter, which resolves to the direct caller of the pool (`msg.sender` from the pool's perspective). When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router's address, not the actual end-user's address. This is structurally inconsistent with `DepositAllowlistExtension`, which correctly checks `owner` (the economic actor). If the router is allowlisted — a natural action for a pool admin who wants to support router-mediated swaps — any unprivileged user can bypass the swap allowlist entirely.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its identity check against `sender`: [1](#0-0) 

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`sender` is forwarded verbatim from `ExtensionCalling._beforeSwap`, which receives it from the pool: [2](#0-1) 

The pool passes its own `msg.sender` (the direct caller) as `sender`. The integration test confirms this: the allowlist is set for `address(callers[0])` — the `TestCaller` contract that directly calls the pool — not for `users[0]`, the actual end-user: [3](#0-2) 

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` checks `owner` — the LP position owner, i.e., the economic actor — not `sender` (the direct caller): [4](#0-3) 

This is the structural inconsistency: deposits correctly gate the economic actor (`owner`) even when the `MetricOmmPoolLiquidityAdder` is the direct caller; swaps gate the direct caller (`sender`), which is the router when users route through `MetricOmmSimpleRouter`.

**Bypass path:**

1. Pool admin deploys a pool with `SwapAllowlistExtension` to restrict swaps to KYC'd or trusted addresses.
2. Pool admin allowlists the `MetricOmmSimpleRouter` as a trusted periphery contract (a natural action to support router-mediated swaps for allowlisted users).
3. Any unprivileged user calls `MetricOmmSimpleRouter.exactInput(...)` or `exactOutput(...)` targeting this pool.
4. The router calls `pool.swap(recipient, ...)` — the pool passes `msg.sender` (router address) as `sender` to the extension.
5. The extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
6. The actual user's address is never checked.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to enforce access control (e.g., KYC compliance, curated market-maker pools, or regulatory gating) can be fully bypassed by any unprivileged user routing through the public `MetricOmmSimpleRouter`. The user receives tokens from the pool; the pool's LP providers absorb the trade. This constitutes a direct policy bypass with fund-impacting consequences: LP assets are exposed to toxic or unauthorized flow that the allowlist was designed to prevent.

---

### Likelihood Explanation

The trigger requires the pool admin to allowlist the router. This is a semi-trusted actor action, but it is a **natural and expected** configuration step: a pool admin who wants to support both direct and router-mediated swaps for their allowlisted users would allowlist the router. The admin has no on-chain signal that doing so opens the allowlist to all users. The `MetricOmmSimpleRouter` is a public, permissionless contract with no internal access controls, so allowlisting it is equivalent to setting `allowAllSwappers = true`.

---

### Recommendation

The `beforeSwap` hook should check the same economic actor that the deposit hook checks. Two options:

1. **Pass the original user address through the router**: Have `MetricOmmSimpleRouter` pass the original `msg.sender` as the `sender` argument to `pool.swap(...)`, so the pool forwards the actual user to the extension. This requires the pool to accept a caller-supplied sender, which changes the trust model.

2. **Align the allowlist check with the deposit pattern**: Gate swaps on `recipient` (the address receiving tokens) rather than `sender` (the direct caller), mirroring how `DepositAllowlistExtension` gates on `owner`. This is imperfect if recipient ≠ swapper, but eliminates the router bypass.

3. **Document and enforce that the router must never be allowlisted**: Add a check in the extension or router that prevents the router address from being added to any pool's swap allowlist.

The cleanest fix is option 1, making the router explicitly forward the originating user address as `sender` to the pool, consistent with how `MetricOmmPoolLiquidityAdder` forwards `owner` for deposits.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls swapExtension.setAllowedToSwap(pool, router, true)
    (intending to allow router-mediated swaps for allowlisted users)
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker (not allowlisted) calls MetricOmmSimpleRouter.exactInput(...)
    targeting the pool
  - Router calls pool.swap(attacker, zeroForOne, amount, priceLimit, extensionData)
  - Pool calls _beforeSwap(msg.sender=router, recipient=attacker, ...)
  - Extension checks allowedSwapper[pool][router] → true → no revert
  - Swap executes; attacker receives tokens

Expected: revert NotAllowedToSwap
Actual:   swap succeeds; allowlist bypassed
``` [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
  }
```

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L68-74)
```text
  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
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
