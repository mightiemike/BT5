### Title
SwapAllowlistExtension gates the router's address instead of the originating user, allowing any user to bypass per-user swap restrictions via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through the shared `MetricOmmSimpleRouter`, `msg.sender` is the router contract, not the original user. Because the router is a single shared public contract, allowlisting it for any router-mediated swap on a curated pool grants swap access to every user who routes through it, defeating the per-user allowlist entirely.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

In `MetricOmmPool.swap()`, the pool calls `_beforeSwap` with `msg.sender` as the first argument: [1](#0-0) 

`_beforeSwap` then encodes that value as `sender` in the ABI call forwarded to every configured extension: [2](#0-1) 

**Step 2 — SwapAllowlistExtension checks `sender` (the direct pool caller) against the per-pool allowlist.**

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Here `msg.sender` is the pool (the extension's caller) and `sender` is the address the pool forwarded — i.e., whoever called `pool.swap()` directly. [3](#0-2) 

**Step 3 — When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`.**

The router is a single shared public contract. Every user who calls `exactInput` / `exactOutput` on the router causes the pool to see `sender = router`. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**Step 4 — The forced choice destroys per-user granularity.**

| Router allowlisted? | Effect |
|---|---|
| No | All router-mediated swaps revert — even for users the admin intended to allow |
| Yes | All users can swap through the router — the per-user allowlist is bypassed |

There is no configuration that simultaneously (a) allows router-mediated swaps for approved users and (b) blocks router-mediated swaps for unapproved users.

**Contrast with DepositAllowlistExtension**, which correctly checks `owner` (the position beneficiary, user-supplied and pool-enforced) rather than `sender` (the payer/router): [4](#0-3) 

The deposit extension gates the economically relevant actor regardless of routing path. The swap extension does not.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific addresses (e.g., KYC-verified users, institutional counterparties) loses that restriction entirely for any user who routes through the public `MetricOmmSimpleRouter`. The disallowed user executes a real swap, moves pool reserves, and receives output tokens — a direct policy bypass with fund-impacting consequences on curated pools.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the standard user-facing entry point documented and deployed for the protocol. Any pool operator who (a) configures a swap allowlist and (b) wants to support router-mediated swaps for their approved users must allowlist the router. Once that allowlist entry exists, every user on the network can bypass the per-user gate. The trigger is a normal public swap call through the router — no special privileges required.

---

### Recommendation

Gate the original user's identity, not the direct pool caller. Two viable approaches:

1. **Pass the original user in `extensionData`**: The router encodes the originating user's address in `extensionData`; `SwapAllowlistExtension` decodes and checks it. This requires a convention between the router and the extension.

2. **Check `recipient` instead of `sender`**: If the pool's design guarantees that `recipient` is always the economic beneficiary of the swap, the extension can check `recipient`. However, this only works if the router always sets `recipient` to the originating user and never to itself.

3. **Align with `DepositAllowlistExtension`'s pattern**: Introduce a separate `originalSwapper` argument to the `beforeSwap` hook (analogous to `owner` in `beforeAddLiquidity`) that the pool populates from a trusted source rather than raw `msg.sender`.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - allowedSwapper[pool][alice] = true   (alice is approved)
  - allowedSwapper[pool][bob]   = false  (bob is NOT approved)
  - allowedSwapper[pool][router] = true  (required for alice to use the router)

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInput(pool, ..., recipient=bob)
  2. Router calls pool.swap(bob, ...)
  3. pool._beforeSwap(msg.sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true → passes
  5. Bob's swap executes; bob receives output tokens

Result:
  Bob, who is explicitly not on the allowlist, completes a swap on a curated pool
  by routing through the shared public router.
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
