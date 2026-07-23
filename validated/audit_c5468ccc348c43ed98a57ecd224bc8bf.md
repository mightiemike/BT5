All four cited files have been verified against the actual production code in the repository. The code matches the claim exactly:

- `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the first argument. [1](#0-0) 
- `ExtensionCalling._beforeSwap` passes `sender` (the pool's `msg.sender`) directly to the extension. [2](#0-1) 
- `MetricOmmPool.swap` passes `msg.sender` as `sender`. [3](#0-2) 
- `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the pool see `msg.sender = router`. [4](#0-3) 

The router never encodes the real user into `extensionData` — it passes `params.extensionData` verbatim from the caller, with no user identity prepended. [5](#0-4) 

The exploit path is fully reachable by an unprivileged trader. The finding is valid.

---

Audit Report

## Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the immediate `msg.sender` of `pool.swap()` against the per-pool allowlist. When swaps are routed through `MetricOmmSimpleRouter`, the pool sees `sender = router`, not the end user. A pool admin who allowlists the router address (the natural step to enable router-mediated swaps for approved users) inadvertently opens the gate to every unprivileged address that calls the router.

## Finding Description
`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the calling pool and `sender` is the immediate caller of `pool.swap()`. `ExtensionCalling._beforeSwap` forwards `sender` directly from `MetricOmmPool.swap`, which passes `msg.sender` — the immediate external caller of the pool. When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap(...)`, the pool's `msg.sender` is the router contract. The router never encodes the real user identity into `extensionData`; it forwards the caller-supplied `params.extensionData` verbatim. Therefore the extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. Any unprivileged address can call the router and pass the allowlist check as long as the router itself is allowlisted.

## Impact Explanation
A pool admin who deploys `SwapAllowlistExtension` to restrict swaps to specific counterparties (e.g., KYC-verified addresses, institutional partners) must also allowlist the router if they want approved users to access the router. Once the router is allowlisted, the per-user gate collapses entirely: any address can call `MetricOmmSimpleRouter.exactInputSingle` or `exactInput` and the extension passes because it sees `sender = router`. LP funds are exposed to unauthorized counterparties, violating the access-control invariant the pool admin configured and potentially causing LP losses from swap patterns the LP did not price for. This constitutes a broken core pool functionality (allowlist guard) causing potential loss of LP assets and a bypass of the admin-configured access boundary by an unprivileged path.

## Likelihood Explanation
Likelihood is medium. `SwapAllowlistExtension` is a production extension explicitly documented as gating "swap by swapper address, per pool." A pool admin who wants their allowlisted users to use the router will naturally allowlist the router address, unaware that doing so opens the gate to all users. The router is a public, permissionless contract, so any attacker can exploit this immediately after the router is allowlisted, with no special privileges or capital requirements beyond the swap input amount.

## Recommendation
The extension should check the ultimate user rather than the immediate caller. The preferred fix is to have the router encode `msg.sender` into a standardized prefix of `extensionData`, and have `SwapAllowlistExtension` detect when `sender` is a known trusted router, decode the real user from `extensionData`, and check that address instead. Alternatively, provide a separate `RouterSwapAllowlistExtension` that reads the real user from a standardized `extensionData` field populated by the router, and document clearly that allowlisting the router in the base `SwapAllowlistExtension` disables per-user gating.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension configured in beforeSwap order
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is the only allowed swapper
  - Pool admin calls setAllowedToSwap(pool, router, true)  // to let alice use the router

Attack:
  - bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle({
        pool: pool,
        zeroForOne: true,
        amountIn: X,
        recipient: bob,
        extensionData: "",
        ...
    })
  - Router calls pool.swap(bob, true, X, ...)  [router is msg.sender]
  - Pool calls _beforeSwap(msg.sender=router, bob, ...)
  - Extension checks allowedSwapper[pool][router] → true → passes
  - Bob's swap executes successfully despite not being allowlisted

Result:
  - bob bypasses SwapAllowlistExtension
  - The pool's access-control invariant is broken
  - LP funds are exposed to unauthorized swap counterparties
```

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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
