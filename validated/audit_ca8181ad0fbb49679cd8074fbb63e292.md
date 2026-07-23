### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Allowing Allowlist Bypass via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` enforces its per-user gate by inspecting the `sender` argument forwarded by the pool, which is `msg.sender` of the `pool.swap()` call. When `MetricOmmSimpleRouter` intermediates the swap, `sender` is the router address, not the actual end user. If the router is allowlisted for a pool (a natural admin action to let permitted users reach the pool through the router), every user—including those not individually allowlisted—can bypass the per-user restriction by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its check as:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct) and `sender` is the value the pool forwarded from its own `msg.sender` — i.e., whoever called `pool.swap()`. When `MetricOmmSimpleRouter` calls `pool.swap()`, the pool's `msg.sender` is the router, so the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

The pool propagates this value unchanged through `ExtensionCalling._beforeSwap`:

```solidity
// metric-core/contracts/ExtensionCalling.sol L149-L176
function _beforeSwap(
    address sender,   // ← pool's msg.sender, i.e. the router
    address recipient,
    ...
) internal {
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
    );
}
```

Compare this with `DepositAllowlistExtension.beforeAddLiquidity`, which correctly checks `owner` (the actual position owner) rather than `sender` (the operator/router):

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L32-L41
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
```

The deposit extension correctly distinguishes operator from owner; the swap extension does not, because there is no separate `owner` field in the swap path — only `sender` (the direct caller) and `recipient` (the output destination).

---

### Impact Explanation

A pool admin who configures `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC-verified traders, institutional counterparties) and then allowlists `MetricOmmSimpleRouter` so those users can reach the pool through the standard periphery inadvertently opens the pool to every user of the router. Any address can call `MetricOmmSimpleRouter`, which calls `pool.swap()` with `msg.sender = router`, causing the extension to evaluate `allowedSwapper[pool][router] = true` and pass the gate. The pool admin's intended per-user access boundary is silently nullified. This constitutes an admin-boundary break: an unprivileged path (routing through the public router) bypasses a pool-admin-configured guard.

---

### Likelihood Explanation

The scenario is realistic: a pool admin who wants to restrict swaps to specific users but still allow those users to use the standard router will naturally allowlist the router. The admin has no way to simultaneously allowlist the router and enforce per-user restrictions, because the extension has no visibility into who initiated the router call. The bypass requires no special privileges — any address can call `MetricOmmSimpleRouter`.

---

### Recommendation

Pass the actual initiating user through the swap path so the extension can check it. One approach: add an `initiator` field to the swap call or `extensionData`, and have `SwapAllowlistExtension` read it. A simpler approach consistent with the deposit pattern: have the router pass the original `msg.sender` as a verified parameter and have the extension check that value instead of (or in addition to) `sender`. At minimum, document clearly that allowlisting the router is equivalent to `allowAllSwappers = true`, so admins are not misled about the security model.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` attached via `beforeSwap` order.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is permitted.
3. Admin calls `setAllowedToSwap(pool, router, true)` — router is allowlisted so Alice can use it.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.swap(pool, ...)`.
5. Router calls `pool.swap(recipient=bob, ...)` with `msg.sender = router`.
6. Pool calls `extension.beforeSwap(sender=router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router] == true` → passes.
8. Bob's swap executes despite not being individually allowlisted, bypassing the admin-configured guard. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-41)
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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L31-35)
```text
  modifier onlyPoolAdmin(address pool_) {
    address poolAdmin = IMetricOmmPoolFactory(FACTORY).poolAdmin(pool_);
    if (msg.sender != poolAdmin) revert OnlyPoolAdmin(pool_, msg.sender, poolAdmin);
    _;
  }
```
