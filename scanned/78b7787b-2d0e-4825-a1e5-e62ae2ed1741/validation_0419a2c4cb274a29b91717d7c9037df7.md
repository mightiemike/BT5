### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Real User, Allowing Any Caller to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the **direct caller of the pool** (`sender = pool's msg.sender`) against the per-pool allowlist. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. If the pool admin allowlists the router (the only way to enable router-mediated swaps for legitimate users), every unprivileged address can bypass the swap allowlist by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument, which the pool sets to `msg.sender` of the `swap` call: [1](#0-0) [2](#0-1) 

The extension then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any of the `exact*` variants), the router calls `pool.swap(...)` directly: [4](#0-3) 

The pool's `msg.sender` is the **router contract address**, so `sender` passed to `beforeSwap` is the router, not the originating EOA. The allowlist lookup becomes `allowedSwapper[pool][router]`.

This creates an irresolvable dilemma for the pool admin:

| Router allowlisted? | Effect |
|---|---|
| **Yes** (to support router users) | Every non-allowlisted EOA bypasses the guard by routing through the router |
| **No** | Every allowlisted EOA is also blocked from using the router |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users.

---

### Impact Explanation

A pool deploying `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, protocol-owned addresses, or whitelisted market makers) loses that protection entirely for any user who routes through `MetricOmmSimpleRouter`. The non-allowlisted user executes a real swap, receives output tokens, and the pool's token balances change exactly as if the allowlist did not exist. This is a direct loss of the curation invariant and constitutes broken core pool functionality for allowlisted pools.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the canonical periphery swap entry point documented and deployed alongside the protocol. Any pool admin who configures `SwapAllowlistExtension` and also wants their allowlisted users to be able to use the router must allowlist the router — at which point the bypass is immediately available to every address. The trigger requires no special privilege: any EOA calls a public router function with a valid swap path.

---

### Recommendation

The extension must gate the **originating user**, not the intermediary. Two viable approaches:

1. **Pass the originating user through the router.** Have `MetricOmmSimpleRouter` forward `msg.sender` as an explicit `sender` field inside `extensionData`, and have `SwapAllowlistExtension.beforeSwap` decode and check that field when `sender` is a known router. This requires a trusted router registry or a signed payload.

2. **Check `sender` and fall back to decoding `extensionData`.** The extension can require that when `sender` is not directly allowlisted, the `extensionData` contains a signed or router-attested originating address that is allowlisted.

3. **Require direct pool calls only.** Document that pools using `SwapAllowlistExtension` must not allowlist the router and must instruct users to call the pool directly. This is the simplest fix but breaks router UX.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin allowlists router: setAllowedToSwap(pool, router, true)
  - Pool admin does NOT allowlist attacker EOA

Attack:
  1. attacker (non-allowlisted EOA) calls:
       MetricOmmSimpleRouter.exactInputSingle({
           pool: pool,
           recipient: attacker,
           zeroForOne: true,
           amountIn: X,
           ...
       })

  2. Router calls pool.swap(attacker, true, X, ...) — msg.sender = router

  3. Pool calls _beforeSwap(router, attacker, ...)
       → ExtensionCalling passes sender = router to SwapAllowlistExtension

  4. SwapAllowlistExtension checks allowedSwapper[pool][router] == true → PASSES

  5. Swap executes; attacker receives output tokens.

Expected: revert NotAllowedToSwap
Actual:   swap succeeds, allowlist bypassed
``` [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-240)
```text
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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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
