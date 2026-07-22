### Title
SwapAllowlistExtension Gates on Router Address Instead of End-User, Allowing Any User to Bypass the Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` enforces its allowlist against `sender`, which is the immediate `msg.sender` of `pool.swap()`. When `MetricOmmSimpleRouter` mediates a swap, `sender` is the router address, not the end user. A pool admin who allowlists the router (required for any router-mediated swap to succeed) inadvertently opens the allowlist to every user on the network, because the extension cannot distinguish between individual end users once the router is the caller.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its check as:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension's caller) and `sender` is the value the pool forwarded from its own `msg.sender`. In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as `sender`:

```solidity
_beforeSwap(
    msg.sender,   // ← this becomes `sender` in the extension
    recipient,
    ...
);
``` [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` of that call:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [3](#0-2) 

The same pattern applies to `exactInput` (all hops), `exactOutputSingle`, and `exactOutput`. In every case the pool receives `msg.sender = router`, so the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

The pool admin faces an impossible choice:

| Admin decision | Effect |
|---|---|
| Do **not** allowlist the router | All router-mediated swaps revert; only direct `pool.swap()` callers work |
| **Allowlist the router** | Every user on the network can swap through the router; the per-user allowlist is completely bypassed |

There is no configuration that simultaneously permits router-mediated swaps and enforces per-user restrictions, because the extension interface carries no trustworthy end-user identity when the router is the intermediary.

The `extensionData` field is caller-controlled and is not used by the extension for identity checks, so it cannot close this gap.

---

### Impact Explanation

A curated pool (e.g., KYC-only, institutional-only, or regulatory-restricted) deploys with `SwapAllowlistExtension` and allowlists the router to support normal UX. Any non-allowlisted user can call `MetricOmmSimpleRouter.exactInputSingle` targeting that pool and execute a swap that the allowlist was supposed to block. The pool's curated access control is silently voided for all router-mediated flows, which is the dominant swap path for end users.

**Impact class**: High — broken core pool functionality (allowlist guard fails open), unauthorized access to restricted pools, potential regulatory or policy violation with direct fund-flow consequences.

---

### Likelihood Explanation

- The router is a standard, publicly deployed periphery contract.
- Any pool that wants to support normal user UX must allowlist the router.
- The bypass requires zero privilege: any EOA can call `exactInputSingle`.
- The pool admin has no on-chain signal that the allowlist is being bypassed; the extension simply sees an allowlisted address (the router).

**Likelihood**: High — the bypass is trivially reachable by any user the moment the router is allowlisted, which is the expected operational state for any pool that supports periphery access.

---

### Recommendation

The extension must gate on the economic actor, not the immediate caller. Two options:

1. **Pass end-user identity through `extensionData` and verify it with a signature or trusted forwarder pattern.** The router would include the original `msg.sender` in `extensionData`; the extension would verify a signature or check a trusted-forwarder registry before accepting it.

2. **Check `sender` only when `msg.sender` (the pool's caller) is not a known router; when it is a router, require the router to attest the real user via a separate trusted channel.** This requires the extension to maintain a registry of trusted routers and a corresponding attestation mechanism.

The simplest safe default is to document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and revert if `msg.sender` (the pool) is called from any address other than a direct EOA — but this breaks composability. The correct fix is option 1 or 2 above.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, router, true)   // allowlist the router
  - Pool admin does NOT allowlist attacker EOA

Attack:
  - attacker (non-allowlisted EOA) calls:
      router.exactInputSingle({
          pool: curated_pool,
          recipient: attacker,
          zeroForOne: true,
          amountIn: X,
          ...
      })

  - router calls pool.swap(recipient, zeroForOne, ...) with msg.sender = router
  - pool calls extension.beforeSwap(sender=router, ...)
  - extension checks: allowedSwapper[pool][router] == true  ✓
  - swap executes; attacker receives output tokens

Result:
  - Non-allowlisted attacker successfully swaps on a curated pool.
  - The allowlist guard is completely bypassed.
``` [4](#0-3) [5](#0-4) [6](#0-5)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L217-241)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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
