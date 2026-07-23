### Title
`SwapAllowlistExtension` gates the immediate pool caller (`sender`) instead of the economic end-user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, that value is the router contract address, not the end-user. A pool admin who allowlists the router (the only way to let their allowlisted users trade via the router) simultaneously opens the gate to every non-allowlisted user on the network.

---

### Finding Description

**Call chain:**

```
User (non-allowlisted)
  → MetricOmmSimpleRouter.exactInputSingle(...)
      → pool.swap(recipient, ..., extensionData)   // msg.sender = router
          → ExtensionCalling._beforeSwap(sender = router, ...)
              → SwapAllowlistExtension.beforeSwap(sender = router, ...)
                  → allowedSwapper[pool][router] == true  ← bypass
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` as `sender` to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← router address when called via router
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap,
        (sender, recipient, ...))   // sender = router
);
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol L37-38
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool; `sender` is the router. The check resolves to `allowedSwapper[pool][router]`.

**The dilemma the pool admin faces:**

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot trade via the router — broken UX |
| **Allowlist the router** | Every user on the network can bypass the allowlist by routing through the router |

There is no configuration that simultaneously allows router-mediated swaps for allowlisted users and blocks non-allowlisted users, because the extension has no visibility into who initiated the router call.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, institutional LPs, or protocol-controlled addresses) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. Non-allowlisted users can execute live swaps against the pool's liquidity, directly exposing LP principal to unauthorized trading flow. This matches the "Admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path" and "Allowlist path: swap allowlist checks must cover the exact actor/action intended and cannot be bypassed through router" criteria.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the canonical periphery swap entry point. Any pool admin who deploys a swap-allowlisted pool and wants their allowlisted users to trade via the router must allowlist the router address. This is a natural, expected operational step. Once taken, the bypass is unconditional and requires no further privilege — any EOA can call `exactInputSingle` or `exactInput` on the router.

---

### Recommendation

The extension must gate the economic actor, not the immediate pool caller. Two viable approaches:

1. **Pass the real initiator in `extensionData`**: The router encodes `msg.sender` (the end-user) into `extensionData`; the extension decodes and checks that address. This requires a coordinated change to the router and the extension's `beforeSwap` logic.

2. **Reject router-mediated swaps on allowlisted pools**: Document that `SwapAllowlistExtension` is incompatible with router usage and enforce this at the extension level by reverting whenever `sender != tx.origin` (with the known caveats of `tx.origin`), or by maintaining a separate registry of trusted forwarders that must themselves attest to the real caller.

---

### Proof of Concept

```solidity
// Setup
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
// Pool deployed with ext as beforeSwap hook

// Admin allowlists the router so their users can trade via it
ext.setAllowedToSwap(pool, address(router), true);

// Non-allowlisted attacker bypasses the allowlist
// ext.isAllowedToSwap(pool, attacker) == false  ← attacker is NOT on the list
router.exactInputSingle(ExactInputSingleParams({
    pool:            pool,
    recipient:       attacker,
    zeroForOne:      true,
    amountIn:        1e18,
    amountOutMinimum: 0,
    priceLimitX64:   0,
    deadline:        block.timestamp,
    extensionData:   ""
}));
// Swap succeeds: pool.swap is called with msg.sender = router,
// extension checks allowedSwapper[pool][router] == true → passes.
// Attacker trades on a pool they were explicitly excluded from.
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
