### Title
`SwapAllowlistExtension.beforeSwap` gates the router address instead of the actual swapper, allowing full allowlist bypass through `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

The `SwapAllowlistExtension` is designed to restrict swaps on curated pools to a configured set of addresses. Its `beforeSwap` hook checks the `sender` argument supplied by the pool, which is `msg.sender` of the pool's `swap` call — the direct caller. When a user routes through `MetricOmmSimpleRouter`, the direct caller is the router, not the user. The allowlist therefore gates the router address, not the economic actor. A pool admin who allowlists the router to enable router-mediated swaps inadvertently opens the gate to every user, defeating the entire curation policy.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its identity check as follows:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct) and `sender` is the first argument forwarded by the pool from `ExtensionCalling._beforeSwap`:

```solidity
// ExtensionCalling.sol L149-L176
function _beforeSwap(
    address sender,   // ← pool passes msg.sender of its own swap() call
    address recipient,
    ...
) internal {
    _callExtensionsInOrder(
        BEFORE_SWAP_ORDER,
        abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
    );
}
```

When a user swaps through `MetricOmmSimpleRouter`, the call chain is:

```
User ──► MetricOmmSimpleRouter.exactInput*(...)
              └──► pool.swap(recipient, zeroForOne, amount, priceLimit, extensionData)
                        msg.sender = router
                        └──► _beforeSwap(sender = router, ...)
                                  └──► SwapAllowlistExtension.beforeSwap(sender = router, ...)
                                            checks allowedSwapper[pool][router]
```

The extension never sees the user's address. It only sees the router's address.

This creates two mutually exclusive failure modes that mirror the `walletMaxLimit` dual-failure pattern from the external report:

| Admin choice | Effect |
|---|---|
| **Do not allowlist the router** | Legitimate allowlisted users cannot swap through the router; core swap UX is broken for them |
| **Allowlist the router** | Every user — including those explicitly excluded — can bypass the allowlist by routing through the router |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users. The guard is structurally misbound to the wrong actor.

---

### Impact Explanation

**Direct loss / broken core functionality — High.**

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties (e.g., KYC'd addresses, institutional partners, or whitelist-only launch participants) loses its curation guarantee the moment any non-allowlisted user discovers they can route through `MetricOmmSimpleRouter`. The pool's LP funds are exposed to trades from actors the pool admin explicitly excluded. Conversely, if the admin does not allowlist the router, allowlisted users are silently locked out of the primary swap interface, breaking the core swap flow for legitimate participants.

---

### Likelihood Explanation

**High.** `MetricOmmSimpleRouter` is the primary user-facing swap entrypoint. Any non-allowlisted user who attempts a direct pool swap and is rejected will naturally try the router next. The bypass requires no privileged access, no special token, and no multi-step setup — a single `exactInput` call through the router suffices. Pool admins who allowlist the router to restore router usability for legitimate users will unknowingly open the gate to all users.

---

### Recommendation

The extension must check the address of the economic actor, not the intermediary. Two sound approaches:

1. **Pass the originating user through `extensionData`**: The router encodes the original `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires the router to be trusted to supply the correct address, which is acceptable given it is a protocol-controlled contract.

2. **Check `recipient` instead of `sender` for router flows, or add a dedicated `originalSender` field to the swap interface**: The pool interface could be extended to carry the originating user address separately from the direct caller, similar to how `owner` is already separated from `sender` in the liquidity hooks.

The `DepositAllowlistExtension` correctly gates on `owner` (the LP position owner, not the adder contract), demonstrating that the pattern of checking the economic actor rather than the intermediary is already understood and applied on the liquidity side — the same discipline must be applied to the swap side.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` attached.
2. Pool admin calls `setAllowedToSwap(pool, userA, true)` — only `userA` is meant to trade.
3. Pool admin also calls `setAllowedToSwap(pool, router, true)` so that `userA` can use the router (otherwise `userA`'s router swaps revert).
4. `userB` (not allowlisted) calls `MetricOmmSimpleRouter.exactInput(...)` targeting the pool.
5. Router calls `pool.swap(recipient=userB, ...)` — pool's `msg.sender` is the router.
6. Pool calls `_beforeSwap(sender=router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true`.
8. `userB`'s swap executes successfully on the supposedly curated pool.

The allowlist is fully bypassed. The only way to prevent step 8 is to remove the router from the allowlist, which simultaneously breaks `userA`'s ability to use the router (step 3 reverts). [1](#0-0) [2](#0-1) [3](#0-2)

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
