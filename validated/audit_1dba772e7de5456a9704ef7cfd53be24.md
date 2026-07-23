### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of the pool's `swap` call. When `MetricOmmSimpleRouter` intermediates the swap, `sender` is the router address, not the actual user. A pool admin who allowlists the router (the natural action to enable standard-periphery access) inadvertently opens the pool to every user, completely defeating the curation invariant.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the pool calls the extension) and `sender` is the first argument forwarded by the pool — which is `msg.sender` of the pool's own `swap` call.

`ExtensionCalling._beforeSwap` passes that value verbatim:

```solidity
// ExtensionCalling.sol L149-176
function _beforeSwap(address sender, ...) internal {
    _callExtensionsInOrder(
        BEFORE_SWAP_ORDER,
        abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
    );
}
```

And the pool calls `_beforeAddLiquidity(msg.sender, ...)` / `_beforeSwap(msg.sender, ...)` — so `sender` is always the immediate caller of the pool, not the originating user.

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutput`, `exactOutputSingle`) calls `pool.swap(...)`:

```solidity
// MetricOmmSimpleRouter.sol L72-80
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

`msg.sender` inside the pool is the **router**, so the extension evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][actualUser]`.

There is no mechanism in the extension or the pool to recover the originating EOA. The router does not forward the user's identity in `extensionData` either.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and then allowlists the router address (the natural action to let their allowlisted users access the pool via the standard periphery) silently opens the pool to **every** user. Any non-allowlisted address can call `MetricOmmSimpleRouter.exactInputSingle` and the extension will pass because it sees the allowlisted router, not the disallowed caller.

LPs in such a curated pool deposit under the assumption that only vetted counterparties trade against them. Unrestricted access exposes them to toxic or adversarial order flow, causing direct LP principal loss through adverse selection — a fund-impacting consequence above the contest threshold.

The inverse failure also exists: if the admin does **not** allowlist the router, allowlisted users are locked out of the standard periphery and must call the pool directly, breaking the expected swap flow.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the canonical, documented swap entry point for end users.
- A pool admin configuring a curated pool will naturally allowlist the router to make the pool usable via the standard UI/SDK.
- No warning or documentation in `SwapAllowlistExtension` alerts the admin to this identity-collapse.
- The exploit requires no special privileges: any EOA calls the public router.

Likelihood: **High** — the misconfiguration is the expected configuration.

---

### Recommendation

The extension must verify the originating user, not the immediate pool caller. Two sound approaches:

1. **Pass user identity through `extensionData`**: The router encodes `msg.sender` into `extensionData` before forwarding to the pool; the extension decodes and checks it. The pool must not allow callers to forge this field (e.g., by requiring the pool to inject `tx.origin` or by having the router sign the payload).

2. **Check `tx.origin` as a fallback**: For EOA-only curated pools, `tx.origin` is the actual user. This is safe when the pool admin explicitly opts in and the pool is not intended for contract callers.

3. **Document the limitation explicitly**: If neither fix is applied, the extension NatSpec must state that allowlisting the router grants access to all router users, and pool admins must allowlist individual EOAs and instruct users to call the pool directly.

---

### Proof of Concept

```
Setup:
  - Pool deployed with SwapAllowlistExtension
  - Admin calls setAllowedToSwap(pool, alice, true)       // alice is allowlisted
  - Admin calls setAllowedToSwap(pool, router, true)      // router allowlisted so alice can use it

Attack:
  1. Bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, ..., extensionData: ""})

  2. Router calls:
       pool.swap(recipient, zeroForOne, amount, limit, "", "")
       // msg.sender inside pool = router

  3. Pool calls:
       _beforeSwap(sender=router, ...)

  4. Extension evaluates:
       allowedSwapper[pool][router] == true  →  check passes

  5. Bob's swap executes against LP funds in the curated pool.

Result:
  Bob, a non-allowlisted user, successfully trades in a pool
  the admin intended to restrict, exposing LPs to unvetted order flow.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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
