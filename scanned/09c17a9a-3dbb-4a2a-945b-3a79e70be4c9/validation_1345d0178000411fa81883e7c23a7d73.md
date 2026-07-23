### Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Enabling Allowlist Bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` validates the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When users route through `MetricOmmSimpleRouter`, `sender` becomes the **router's address**, not the end user's address. This creates two fund-impacting outcomes: (1) if the router is allowlisted, every user on the internet can bypass the curated-pool gate; (2) if the router is not allowlisted, allowlisted users cannot use the supported periphery router at all, breaking core swap functionality.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

The pool populates `sender` with its own `msg.sender` at the call site:

```solidity
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the pool's `msg.sender` the **router contract**, not the originating EOA:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData   // user-supplied; router injects nothing about the real user
    );
``` [3](#0-2) 

The router stores the original `msg.sender` only in transient callback context for payment settlement — it is **never forwarded** into `extensionData` or any argument visible to the extension. The extension therefore evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][endUser]`. [4](#0-3) 

---

### Impact Explanation

**Path A — Allowlist bypass (High):** A pool admin who wants to support router-mediated swaps for their allowlisted users must allowlist the router address. Once `allowedSwapper[pool][router] = true`, the extension passes for **any** caller who routes through `MetricOmmSimpleRouter`, regardless of whether that caller is on the allowlist. Non-KYC'd or otherwise excluded users can freely trade on a curated pool by simply using the supported periphery router.

**Path B — Broken core functionality (Medium):** If the pool admin does not allowlist the router, every allowlisted user who calls `router.exactInputSingle()` or `router.exactInput()` receives `NotAllowedToSwap`. The supported periphery swap path is completely unusable for any pool that has `SwapAllowlistExtension` active, even for users the admin explicitly approved.

Both outcomes violate the invariant that the allowlist gates the economically relevant actor on every supported public entrypoint. [5](#0-4) 

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap entrypoint documented in the protocol. Any pool that deploys `SwapAllowlistExtension` and expects users to interact through the router will immediately encounter one of the two failure modes. A pool admin who discovers that allowlisted users cannot use the router will naturally add the router to the allowlist as a fix — triggering the bypass. The trigger requires no privileged access beyond normal pool usage and no non-standard tokens. [6](#0-5) 

---

### Recommendation

The extension must check the **original initiating user**, not the direct caller of `pool.swap()`. Two viable approaches:

1. **Encode the real user in `extensionData`:** The router encodes `msg.sender` into the `extensionData` it forwards, and the extension decodes and validates that address. This requires a convention between the router and the extension.
2. **Check `sender` only for direct pool calls; reject router calls:** The extension can detect that `sender` is a known router and revert unless the pool is configured to allow router-mediated swaps with a separate flag.
3. **Allowlist the router with a per-user sub-check:** Introduce a two-level check where the router is a trusted forwarder and the extension reads the real user from a standardized field in `extensionData`.

The simplest safe fix is for `MetricOmmSimpleRouter` to always prepend `abi.encode(msg.sender)` to `extensionData` before forwarding, and for `SwapAllowlistExtension` to detect and decode this prefix when `sender` is a known router. [1](#0-0) [3](#0-2) 

---

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension in BEFORE_SWAP_ORDER
  admin allowlists router: extension.setAllowedToSwap(pool, address(router), true)
  attacker = address not in allowedSwapper[pool]

Attack:
  attacker calls router.exactInputSingle({
      pool:          pool,
      recipient:     attacker,
      zeroForOne:    true,
      amountIn:      X,
      extensionData: ""
  })

  router → pool.swap(recipient, zeroForOne, amount, limit, "", "")
    pool: msg.sender = router
    pool calls _beforeSwap(router, ...)
    extension: allowedSwapper[pool][router] == true  ← passes
    swap executes; attacker receives output tokens

Result:
  attacker (not on allowlist) successfully swaps on a curated pool.
  The allowlist guard is fully bypassed via the supported periphery router.
``` [4](#0-3) [2](#0-1) [3](#0-2)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-41)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }

  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
  }

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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
```text
    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
      extensionData
    );
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
