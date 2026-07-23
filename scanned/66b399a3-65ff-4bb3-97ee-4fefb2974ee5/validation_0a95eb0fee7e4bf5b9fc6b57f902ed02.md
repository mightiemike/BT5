### Title
`SwapAllowlistExtension.beforeSwap` checks the router's address instead of the actual end-user, allowing any user to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is designed to gate swaps by swapper address, per pool. However, the `sender` argument it receives is the pool's `msg.sender` — which is the **router contract**, not the actual end-user, when swaps are routed through `MetricOmmSimpleRouter`. If the router address is allowlisted for a pool, every user can bypass the per-user allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, recipient, ...)`, forwarding its own `msg.sender` as the `sender` argument to every configured extension. [1](#0-0) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` directly: [2](#0-1) 

Inside the pool, `msg.sender` is the **router address**. That router address is what gets forwarded as `sender` to `SwapAllowlistExtension.beforeSwap`.

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` is the pool and `sender` is the router. The check resolves to `allowedSwapper[pool][router]`. If the router is allowlisted, the gate passes for **every user** regardless of their individual allowlist status.

The contract's own NatSpec states it "Gates `swap` by swapper address, per pool," and the research pivot explicitly flags this path:

> *"Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting."* [4](#0-3) 

The `onlyPool` modifier in `BaseMetricExtension` only verifies the caller is a registered pool — it does not help recover the original user identity: [5](#0-4) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a specific set of addresses (e.g., institutional market makers, KYC'd users) is fully bypassed for any user who routes through `MetricOmmSimpleRouter`. The pool admin has no way to simultaneously (a) allow router-mediated swaps for allowlisted users and (b) block non-allowlisted users from the router, because the extension cannot distinguish the two cases — it only sees the router address.

Unauthorized swaps on a restricted pool can cause direct LP principal loss through adverse selection, price impact, or violation of the pool's intended trading regime.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary public entry point for swaps. A pool admin who wants allowlisted users to be able to use the router UI must allowlist the router address. Once the router is allowlisted, the bypass is unconditional and requires no special privileges — any address can call `exactInputSingle` or `exactInput` on the router.

---

### Recommendation

Pass the **original end-user** identity through the swap call so extensions can gate on it. Two concrete approaches:

1. **Add a `payer`/`originator` field to the swap call** that the pool forwards to extensions alongside `sender`, letting `SwapAllowlistExtension` check the originator rather than the immediate caller.
2. **Encode the real user in `extensionData`** and have the extension verify a signature or a router-attested identity — though this requires router cooperation and is more complex.

The simplest safe fix is to have `MetricOmmSimpleRouter` encode `msg.sender` into `extensionData` and have `SwapAllowlistExtension` decode and verify it when the immediate `sender` is a known router, or to redesign the hook interface to carry an `originator` field distinct from `sender`.

---

### Proof of Concept

```
1. Deploy a pool with SwapAllowlistExtension as a beforeSwap hook.
2. Pool admin calls setAllowedToSwap(pool, alice, true)  — only alice is allowed.
3. Pool admin calls setAllowedToSwap(pool, router, true) — router allowlisted so alice can use the UI.
4. Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...}).
5. Router calls pool.swap(recipient, ...) → pool's msg.sender = router.
6. Pool calls _beforeSwap(router, ...) → extension checks allowedSwapper[pool][router] = true → passes.
7. Bob's swap executes on the restricted pool, bypassing the per-user allowlist entirely.
``` [3](#0-2) [6](#0-5) [7](#0-6)

### Citations

**File:** metric-core/contracts/ExtensionCalling.sol (L148-177)
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
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

**File:** generate_scanned_questions.py (L657-663)
```python
            file_function="metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*",
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
        ),
```

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L19-24)
```text
  modifier onlyPool() {
    if (!IMetricOmmPoolFactory(FACTORY).isPool(msg.sender)) {
      revert OnlyPool(msg.sender, FACTORY);
    }
    _;
  }
```
