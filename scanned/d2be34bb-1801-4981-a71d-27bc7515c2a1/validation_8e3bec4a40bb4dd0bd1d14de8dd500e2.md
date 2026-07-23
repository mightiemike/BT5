### Title
`SwapAllowlistExtension.beforeSwap` checks the router's address as `sender` instead of the actual end user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension` gates swaps by checking the `sender` argument passed by the pool. The pool always sets `sender = msg.sender` of the `swap` call. When users route through `MetricOmmSimpleRouter`, `msg.sender` inside `pool.swap` is the router contract, not the actual user. If the pool admin allowlists the router to enable legitimate router-mediated swaps, every unpermissioned user can bypass the curated pool's allowlist by routing through the public router.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

`MetricOmmPool.swap` unconditionally passes `msg.sender` as the first argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim as the `sender` argument to every configured extension: [2](#0-1) 

**Step 2 — `SwapAllowlistExtension` gates on that `sender` value.**

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

`msg.sender` here is the pool (correct); `sender` is whatever the pool passed — which is the router address when the call originates from the router. [3](#0-2) 

**Step 3 — The router calls `pool.swap` directly, substituting itself as `msg.sender`.**

`exactInputSingle`, `exactInput`, `exactOutputSingle`, and `exactOutput` all call `pool.swap(...)` with the router as the transaction originator: [4](#0-3) [5](#0-4) 

The extension therefore receives `sender = router_address`, not the actual end user.

**Step 4 — The inescapable dilemma.**

| Pool admin action | Consequence |
|---|---|
| Does **not** allowlist the router | Allowlisted users cannot use the router at all — broken core swap flow |
| **Allowlists the router** | `allowedSwapper[pool][router] = true` → every user bypasses the allowlist by routing through the public router |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same.

---

### Impact Explanation

A pool admin who deploys `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties, institutional LPs, or protocol-controlled addresses) cannot enforce that restriction for router-mediated swaps. Any unpermissioned user can call `MetricOmmSimpleRouter.exactInputSingle` and trade on the curated pool, bypassing the admin-configured access boundary. This constitutes a direct admin-boundary break with fund-impacting consequences: unauthorized users gain access to pool liquidity and pricing that was intentionally restricted.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is a public, permissionless contract. No special role or privilege is required to call it. Any user who discovers the allowlist restriction on a direct `pool.swap` call can trivially route through the router instead. The bypass requires zero additional setup.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the actual end user, not the intermediary. Two sound approaches:

1. **Extension-data forwarding**: The router encodes the real user's address into `extensionData`; the extension decodes and verifies it, and also verifies that `msg.sender` (the pool's caller) is a trusted router registered with the factory.
2. **Factory-registered router whitelist**: The factory tracks approved routers; the extension, when `sender` is a known router, decodes the real user from `extensionData` and checks that address against the allowlist.

The `DepositAllowlistExtension` avoids this problem by gating on `owner` (the position beneficiary) rather than `sender` (the payer), which is the correct pattern for the deposit side. [6](#0-5) 

---

### Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension.
2. Pool admin allowlists Alice:
       swapExtension.setAllowedToSwap(pool, alice, true)
3. Pool admin allowlists the router so Alice can use it:
       swapExtension.setAllowedToSwap(pool, router, true)
4. Bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, recipient: bob, ...})
5. Router executes:
       pool.swap(bob, zeroForOne, amount, limit, "", extensionData)
   → msg.sender inside pool.swap = router
6. Pool calls:
       _beforeSwap(router, bob, ...)
7. SwapAllowlistExtension checks:
       allowedSwapper[pool][router]  →  true  (step 3)
8. Bob's swap succeeds — allowlist fully bypassed.
```

Root cause: `MetricOmmPool.swap` passes `msg.sender` (the router) as `sender` to the extension hook, and `SwapAllowlistExtension.beforeSwap` treats that intermediary address as the identity to gate, making the allowlist trivially bypassable through any public periphery contract. [7](#0-6) [8](#0-7) [4](#0-3)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-241)
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
