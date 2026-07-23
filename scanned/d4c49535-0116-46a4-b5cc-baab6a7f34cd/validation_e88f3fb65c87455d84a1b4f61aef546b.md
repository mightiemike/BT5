### Title
`SwapAllowlistExtension` Swap Guard Bypassed via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so `sender` = router address. The extension therefore checks whether the **router** is allowlisted, not the actual end user. Any non-allowlisted user can bypass the swap guard by calling the public, permissionless router.

---

### Finding Description

**Call chain:**

```
User (non-allowlisted)
  → MetricOmmSimpleRouter.exactInputSingle()
      → pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)
          // msg.sender = router
          → _beforeSwap(msg.sender=router, ...)
              → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                  // checks allowedSwapper[pool][router]
                  // NOT allowedSwapper[pool][user]
```

In `MetricOmmPool.swap`, `msg.sender` is forwarded as `sender` to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
  msg.sender,   // ← direct caller of pool.swap(); router when routed
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` passes this `sender` verbatim to the extension:

```solidity
// ExtensionCalling.sol:160-176
_callExtensionsInOrder(
  BEFORE_SWAP_ORDER,
  abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]` where `msg.sender` = pool and `sender` = router:

```solidity
// SwapAllowlistExtension.sol:37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

The router is a fully public, permissionless contract with no access control on who may call `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput`. It does not forward the original user's identity to the pool.

The pool admin faces a structural dilemma:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all; they must call `pool.swap()` directly |
| **Allowlist the router** | Every user on the network can bypass the allowlist by routing through the router |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users. The guard is structurally broken for the router path.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to specific addresses (e.g., KYC-verified counterparties, whitelisted institutions, or protocol-controlled addresses) can be freely accessed by any unprivileged user via the public router. The attacker executes swaps at live oracle prices against pool liquidity that was intended to be restricted, causing:

- Direct loss of LP assets: LPs deposited under the assumption that only allowlisted counterparties could trade against them; non-allowlisted users drain liquidity at oracle prices.
- Broken core pool functionality: the swap allowlist — a production extension explicitly designed to gate the swap path — is rendered completely ineffective for the router path.

---

### Likelihood Explanation

- The router (`MetricOmmSimpleRouter`) is a public, deployed periphery contract with no access control.
- Any user can call `exactInputSingle` or `exactInput` targeting any pool.
- The pool admin must allowlist the router to enable router-mediated swaps for legitimate users; once done, the bypass is open to everyone.
- No special privileges, flash loans, or complex setup are required — a single `exactInputSingle` call suffices.

---

### Recommendation

The extension must gate on the **actual end user**, not the direct pool caller. Two viable approaches:

1. **Check `recipient` instead of `sender`** — for single-hop swaps the recipient is the economic beneficiary; however this breaks for multi-hop where intermediate recipients are the router itself.

2. **Require the router to forward user identity via `extensionData`** — the extension decodes the original `msg.sender` from `extensionData` and checks that address. The router must be updated to encode `msg.sender` into `extensionData` before calling the pool.

3. **Gate on `recipient` for single-hop and require explicit user identity in `extensionData` for multi-hop** — the most robust approach.

The simplest safe fix: update `SwapAllowlistExtension.beforeSwap` to decode an authenticated user address from `extensionData` when `sender` is a known router, and update the router to always encode `msg.sender` into the extension payload it forwards.

---

### Proof of Concept

```solidity
// Pool is configured with SwapAllowlistExtension.
// Admin allowlists Alice (0xAlice) but NOT Bob (0xBob).
// Admin also allowlists the router so Alice can use it.

// Bob (non-allowlisted) calls the router directly:
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:            address(pool),
        tokenIn:         address(token0),
        tokenOut:        address(token1),
        zeroForOne:      true,
        amountIn:        1_000e18,
        amountOutMinimum: 0,
        recipient:       address(bob),
        deadline:        block.timestamp + 1,
        priceLimitX64:   0,
        extensionData:   ""
    })
);
// pool.swap() is called with msg.sender = router
// beforeSwap receives sender = router (allowlisted) → passes
// Bob's swap executes against restricted LP liquidity
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
