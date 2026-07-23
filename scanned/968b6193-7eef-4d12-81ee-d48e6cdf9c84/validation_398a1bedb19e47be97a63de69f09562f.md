### Title
`SwapAllowlistExtension` gates the router address instead of the actual user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the direct caller of `pool.swap()` as the swapper identity. When swaps are routed through the public `MetricOmmSimpleRouter`, the router's address is what the extension sees as `sender`. If the pool admin allowlists the router to enable router-mediated swaps for their permitted users, every non-allowlisted user can bypass the restriction by routing through the same public router contract.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter` is a public, permissionless contract. When a user calls any `exact*` function on the router, the router calls `pool.swap(...)` directly, making itself `msg.sender` to the pool. The extension therefore receives `sender = router`, not the actual end-user.

This creates an irreconcilable dilemma for the pool admin:

| Admin choice | Effect |
|---|---|
| Allowlist individual user addresses only | Allowlisted users cannot use the router (router not in allowlist → revert) |
| Allowlist the router to enable router-mediated swaps | Every non-allowlisted user can bypass the restriction by calling the router |

There is no configuration that simultaneously (a) allows allowlisted users to swap via the router and (b) blocks non-allowlisted users from doing the same. The guard is structurally broken for the router path.

The `DepositAllowlistExtension` does not share this flaw because it checks `owner` (the position beneficiary), which the pool passes explicitly and which the `MetricOmmPoolLiquidityAdder` preserves correctly: [4](#0-3) 

---

### Impact Explanation

A pool admin who deploys a `SwapAllowlistExtension`-gated pool and allowlists the router (a natural step to support standard UX) inadvertently opens the pool to unrestricted swaps by any address. Non-allowlisted users can drain LP positions at oracle-derived prices, bypassing any KYC, compliance, or rate-limiting intent encoded in the allowlist. Because swaps settle real token transfers and the pool has no secondary check on the swapper identity, the loss of LP principal is direct and immediate.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing entry point documented in the protocol. Any pool admin who wants their allowlisted users to have a normal UX will allowlist the router. The bypass requires no special privileges, no flash loans, and no unusual token behavior — only a standard router call. It is reachable by any EOA or contract on every chain the protocol deploys to.

---

### Recommendation

Pass the original user's address through the router to the pool, and have the pool forward it to extensions as a separate `originator` field. Alternatively, `SwapAllowlistExtension` should read the actual user from `extensionData` when `sender` is a known router, or the pool should expose a trusted-forwarder pattern so the router can attest the real caller. The simplest safe fix is to check `sender` only when `sender` is not a registered router, and require the router to sign or encode the real user in `extensionData` for the extension to verify.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, alice, true)       // alice is allowlisted
  admin calls setAllowedToSwap(pool, router, true)      // router allowlisted for UX

Attack (bob, not allowlisted):
  bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  router calls pool.swap(bob_recipient, ...)
    msg.sender to pool = router
  pool calls extension.beforeSwap(router, ...)
    allowedSwapper[pool][router] == true  → passes
  swap executes, bob receives tokens from LP positions
  bob was never in the allowlist
``` [5](#0-4) [1](#0-0)

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
