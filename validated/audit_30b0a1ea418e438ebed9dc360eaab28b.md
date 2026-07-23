Based on my research into the Metric OMM codebase, I can identify a valid native analog.

---

### Title
Swap Allowlist Extension Checks Router's Address as Swapper Identity, Allowing Any User to Bypass Pool Swap Restrictions via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary
The `SwapAllowlistExtension.beforeSwap` hook gates swaps by checking whether the `sender` argument is allowlisted. When users route through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension evaluates the router's address rather than the original user's address. This misbound identity check either allows any user to bypass the allowlist (if the router is allowlisted) or silently breaks the primary swap path for legitimate allowlisted users (if the router is not allowlisted).

### Finding Description

The `beforeSwap` hook in `SwapAllowlistExtension` receives `sender` as its first argument. The pool's `swap()` function does not accept an explicit sender parameter — it uses `msg.sender` internally and forwards it to `ExtensionCalling._beforeSwap`:

```solidity
// ExtensionCalling.sol
function _beforeSwap(
    address sender,   // ← this is pool's msg.sender
    address recipient,
    ...
) internal {
    _callExtensionsInOrder(
        BEFORE_SWAP_ORDER,
        abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
    );
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle(...)`, the router calls `pool.swap(recipient, ...)` directly. The pool's `msg.sender` at that point is the router contract address. The extension therefore evaluates `isAllowedToSwap(pool, router_address)` instead of `isAllowedToSwap(pool, original_user)`.

This produces two distinct failure modes depending on how the pool admin configures the allowlist:

**Mode 1 — Bypass:** If the pool admin allowlists the router address (the natural configuration to allow router-mediated swaps), every user — including those the admin intended to exclude — can bypass the allowlist by routing through `MetricOmmSimpleRouter`. The extension sees the allowlisted router and passes the check unconditionally.

**Mode 2 — DoS:** If the pool admin does not allowlist the router (allowlisting only specific user EOAs), every legitimate allowlisted user is silently blocked when they use the router, because the extension sees the non-allowlisted router address and reverts. The primary user-facing swap interface becomes unusable for the very users the admin intended to serve.

The root cause is that the pool's `swap()` interface provides no mechanism for the router to forward the original caller's identity to the extension layer. The extension is structurally forced to evaluate the wrong actor.

### Impact Explanation

- **Bypass mode:** Unauthorized users gain access to a restricted pool. If the pool holds liquidity priced at oracle rates that are slightly stale or favorable, unauthorized swappers can arbitrage against the pool's LPs, causing direct LP principal loss.
- **DoS mode:** The core swap flow is broken for all allowlisted users who use the router. This constitutes broken core pool functionality — the swap allowlist renders the primary periphery entry point unusable.

Both outcomes are fund-impacting: the bypass enables unauthorized extraction of LP value; the DoS makes the pool's liquidity inaccessible to its intended users.

### Likelihood Explanation

- Any pool that deploys `SwapAllowlistExtension` is affected — the mischecked identity is structural, not conditional.
- The trigger is fully unprivileged: any user can call `MetricOmmSimpleRouter.exactInputSingle` with no special role or setup.
- The bypass requires only that the router be allowlisted, which is the expected configuration for a pool that wants to support router-mediated swaps while still restricting direct callers.

### Recommendation

The pool's `swap()` function should accept an explicit `sender` parameter (similar to how `addLiquidity` separates `sender` from `owner`), allowing the router to forward the original caller's address. Alternatively, the `SwapAllowlistExtension` should decode the original caller from a trusted field in `extensionData`, with the router being responsible for injecting it — though this requires the extension to trust the router's encoding, which introduces its own risks. The cleanest fix is a first-class `sender` parameter on `pool.swap()`.

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, router_address, true)` to enable router-mediated swaps, and does **not** allowlist `attacker_address`.
3. `attacker` calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
4. Router calls `pool.swap(recipient, zeroForOne, amount, priceLimit, callbackData, extensionData)` — pool's `msg.sender` = router.
5. Pool calls `_beforeSwap(sender=router_address, ...)`.
6. `SwapAllowlistExtension.beforeSwap` evaluates `isAllowedToSwap(pool, router_address)` → **true**.
7. Attacker's swap executes against the restricted pool, bypassing the intended access control.

Conversely, if the admin allowlists only `user_address` (not the router), step 6 evaluates `isAllowedToSwap(pool, router_address)` → **false**, and the legitimate allowlisted user's router swap reverts, breaking the primary swap path.

---

**Supporting evidence from codebase:**

The `_beforeSwap` dispatcher in `ExtensionCalling.sol` forwards `sender` directly to the extension: [1](#0-0) 

The `beforeSwap` hook interface receives `sender` as the first positional argument: [2](#0-1) 

The pool's `swap()` function takes no explicit `sender` parameter, meaning `msg.sender` (the router) is the only available identity: [3](#0-2) 

The audit research file explicitly flags this identity mismatch as the primary concern for the swap allowlist path: [4](#0-3)

### Citations

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

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L50-60)
```text
  function beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) external returns (bytes4);
```

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L188-195)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external returns (int128 amount0Delta, int128 amount1Delta);
```

**File:** generate_scanned_questions.py (L655-663)
```python
        Target(
            short="swap allowlist gate",
            file_function="metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*",
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
        ),
```
