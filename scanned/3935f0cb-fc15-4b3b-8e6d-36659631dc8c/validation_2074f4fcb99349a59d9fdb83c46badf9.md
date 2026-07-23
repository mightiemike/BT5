### Title
`SwapAllowlistExtension` checks router address as swapper instead of original user, enabling allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the original user. The extension therefore checks whether the **router** is allowlisted, not the actual swapper. This is the direct analog of the external report's wrong-index check: the guard is applied to the wrong identity, making the allowlist either universally bypassable (if the router is allowlisted) or unusable with the router (if it is not).

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` populates `sender` with whatever the pool passes as its first argument, which is the pool's own `msg.sender` — the direct caller of `pool.swap()`: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` directly, making the router the pool's `msg.sender`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
``` [3](#0-2) 

The same pattern holds for `exactInput` (multi-hop), `exactOutputSingle`, and `exactOutput`: [4](#0-3) 

So when any user routes through the router, the extension sees `sender = router address` and evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][original_user]`.

Contrast this with `DepositAllowlistExtension.beforeAddLiquidity`, which correctly ignores `sender` and checks `owner` — the economically relevant LP actor:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
``` [5](#0-4) 

`SwapAllowlistExtension` has no equivalent correction: it checks `sender` unconditionally, which resolves to the router for all router-mediated swaps.

---

### Impact Explanation

**High.** A pool admin who deploys a `SwapAllowlistExtension` to restrict trading to a curated set of addresses faces an impossible choice:

1. **Do not allowlist the router** → every allowlisted user who calls through `MetricOmmSimpleRouter` is blocked, breaking the supported periphery path.
2. **Allowlist the router** → every address in existence can bypass the allowlist by routing through the router, because the extension sees `sender = router` and passes the check.

In case 2, the allowlist provides zero protection: any non-allowlisted attacker calls `exactInputSingle` or `exactInput` on the router and the extension approves the swap. The attacker trades on a pool that was intended to be restricted, potentially draining liquidity or extracting value from a curated pool whose LP terms assumed a controlled counterparty set.

---

### Likelihood Explanation

**Medium.** The trigger requires the router to be allowlisted on the pool. Pool admins who want their allowlisted users to be able to use the standard periphery router will naturally add the router to the allowlist — this is the expected operational pattern. Once the router is allowlisted, the bypass is unconditional and requires no special privileges or exotic token behavior.

---

### Recommendation

Gate on the actor the pool admin actually intends to restrict. Two options:

**Option A** — Check `recipient` (the address receiving swap proceeds, which is the user-supplied address in all router calls):

```diff
- function beforeSwap(address sender, address, bool, ...)
+ function beforeSwap(address, address recipient, bool, ...)
  {
-     if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
+     if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][recipient]) {
```

**Option B** — Require the original caller to be passed explicitly in `extensionData` and verify it against the allowlist, with the router forwarding `msg.sender` in the payload.

Option A is simpler and consistent with how `DepositAllowlistExtension` uses `owner` (the economically relevant actor) rather than `sender`.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured as a `beforeSwap` hook.
2. Admin calls `setAllowedToSwap(pool, router, true)` so that legitimate users can use the router (or admin calls `setAllowedToSwap(pool, router, true)` because they want to support router-based swaps).
3. Attacker (address `0xBAD`, not in the allowlist) calls `MetricOmmSimpleRouter.exactInputSingle` with `pool` as the target.
4. The router calls `pool.swap(recipient, ...)` — pool's `msg.sender` = router.
5. Pool calls `extension.beforeSwap(router, recipient, ...)`.
6. Extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
7. Attacker successfully swaps on a pool that was supposed to be restricted to allowlisted addresses only.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
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
