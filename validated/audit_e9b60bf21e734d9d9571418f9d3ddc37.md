Let me check how `ExtensionCalling` passes the sender to the extension hook.

The call chain is fully traceable. Here is the assessment:

---

### Title
Router-Mediated Swaps Bypass `SwapAllowlistExtension` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension::beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to its own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router**, not the original user. If the router is allowlisted on a restricted pool, any unprivileged user can bypass the swap allowlist entirely by routing through it.

---

### Finding Description

**Step 1 — Pool passes its own `msg.sender` as `sender` to the hook.**

In `MetricOmmPool::swap`, the pool calls:

```solidity
_beforeSwap(
  msg.sender,   // ← whoever called pool.swap()
  recipient,
  ...
);
``` [1](#0-0) 

`ExtensionCalling::_beforeSwap` then ABI-encodes that `sender` and forwards it to every configured extension: [2](#0-1) 

**Step 2 — The extension checks `allowedSwapper[pool][sender]`.**

`SwapAllowlistExtension::beforeSwap` uses `msg.sender` (the pool) as the pool key and the `sender` argument as the swapper identity:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

**Step 3 — The router calls `pool.swap()` directly, so `sender` = router.**

`exactInputSingle` (and all other `exact*` entry points) call the pool directly:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ...);
``` [4](#0-3) 

The original caller's address (`msg.sender`) is stored only in transient callback context for payment purposes — it is **never forwarded to the pool as the swap initiator**. The pool sees `msg.sender = router`.

**Result:** The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][originalUser]`.

---

### Impact Explanation

Two concrete failure modes arise:

| Scenario | Effect |
|---|---|
| Pool admin allowlists individual users but NOT the router | Allowlisted users cannot use the router; they must call the pool directly. Router is effectively broken for restricted pools. |
| Pool admin allowlists the router (to enable router-mediated swaps) | **Any** unprivileged user bypasses the allowlist by calling any `exact*` router function. The entire access control is nullified. |

The second scenario is the exploit path: an attacker not in the allowlist calls `MetricOmmSimpleRouter::exactInputSingle` targeting a restricted pool where the router is allowlisted. The hook passes, and the unauthorized swap executes. This breaks the core invariant that only approved addresses may swap on a restricted pool.

---

### Likelihood Explanation

Any pool that (a) uses `SwapAllowlistExtension` and (b) expects users to interact via the router faces this issue. The pool admin has no safe configuration: allowlisting the router opens the bypass; not allowlisting it breaks the router for all users. The vulnerability is structural and requires no special attacker capability beyond calling the public router.

---

### Recommendation

The router must forward the original caller's identity to the pool so the extension can check it. Two approaches:

1. **Pass original caller via `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling `pool.swap`. The extension reads and verifies it (with a pool-level trust policy for the router).
2. **Dedicated `sender` override parameter**: Add an explicit `swapper` field to the pool's `swap` interface that the router populates with `msg.sender`, and the pool passes it as `sender` to hooks instead of its own `msg.sender`.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // only alice is allowed
3. Pool admin calls setAllowedToSwap(pool, router, true)  // router must be allowed for UX
4. Bob (not allowlisted) calls:
     MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
5. Pool calls _beforeSwap(msg.sender=router, ...)
6. Extension checks allowedSwapper[pool][router] → true → passes
7. Bob's swap executes despite not being in the allowlist.

Assert: Bob received output tokens. The allowlist invariant is violated.
``` [5](#0-4) [6](#0-5)

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
