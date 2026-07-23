### Title
SwapAllowlistExtension Gates on the Router Address Instead of the End User, Allowing Any Caller to Bypass the Per-User Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of `pool.swap()`. When `MetricOmmSimpleRouter` intermediates the call, `sender` equals the router address, not the actual end user. A pool admin who allowlists the router to enable router-based swaps for their curated users inadvertently opens the pool to every user who routes through the router, completely defeating the per-user allowlist.

---

### Finding Description

`MetricOmmPool.swap()` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this value unchanged as the first argument to every configured extension:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
``` [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks that `sender` (the direct pool caller) is on the allowlist keyed by pool (`msg.sender` inside the extension = the pool):

```solidity
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutput`) calls `pool.swap(...)`, the pool's `msg.sender` is the **router**, not the end user:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [4](#0-3) 

The router stores the actual end user only in transient storage for the payment callback — it is never surfaced to the extension. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][endUser]`.

**The dilemma this creates for the pool admin:**

| Admin action | Result |
|---|---|
| Allowlist individual users only | Allowlisted users cannot swap through the router (router not allowlisted → `NotAllowedToSwap`) |
| Allowlist the router | Every user on-chain can swap through the router; per-user allowlist is nullified |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC'd market makers, whitelisted institutions, or protocol-controlled addresses) cannot enforce that policy when the router is involved. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` targeting the pool and, if the router is allowlisted, execute swaps that the allowlist was designed to block. This directly exposes LP principal to unrestricted arbitrage or adversarial trading that the pool operator explicitly tried to prevent.

---

### Likelihood Explanation

The router is the standard user-facing entrypoint for swaps. A pool admin who configures a swap allowlist and also wants their allowlisted users to access the router will naturally allowlist the router address — this is the only way to make router-based swaps work for any user. The bypass is therefore reachable through a routine, non-malicious admin configuration step. Any unprivileged user who discovers the router is allowlisted can exploit it immediately with a single public transaction.

---

### Recommendation

The extension must resolve the actual end user rather than the direct pool caller. Two approaches:

1. **Pass the real user in `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated convention between router and extension.
2. **Gate on `recipient` or a signed identity**: Require the actual trader to be identified in a field the extension can verify (e.g., a signed permit embedded in `extensionData`).
3. **Separate router allowlist from user allowlist**: Maintain a two-level check — if `sender` is an allowlisted router, decode and verify the end user from `extensionData`; otherwise check `sender` directly.

---

### Proof of Concept

```solidity
// Pool deployed with SwapAllowlistExtension as beforeSwap hook.
// Admin allowlists the router so that their approved users can swap via router.
swapAllowlist.setAllowedToSwap(address(pool), address(router), true);

// Attacker (not on allowlist) calls the router directly.
// The extension sees sender = router → allowedSwapper[pool][router] = true → passes.
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    recipient: attacker,
    zeroForOne: false,
    amountIn: largeAmount,
    amountOutMinimum: 0,
    priceLimitX64: type(uint128).max,
    deadline: block.timestamp,
    tokenIn: token1,
    extensionData: ""
}));
// Attacker successfully swaps despite not being on the per-user allowlist.
// LP funds are exposed to unrestricted trading.
```

### Citations

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
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
```
