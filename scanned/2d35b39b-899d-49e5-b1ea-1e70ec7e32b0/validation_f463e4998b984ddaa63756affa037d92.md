### Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Allowing Any User to Bypass Swap Allowlist via Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool, which is `msg.sender` at the pool level — the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. The extension therefore checks whether the **router** is allowlisted, not whether the **actual user** is allowlisted. If the router is allowlisted (a natural operational requirement so that allowlisted users can use the router at all), every unprivileged user can bypass the curated pool's swap restriction by routing through it.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exact*`, the router calls `pool.swap(...)` — making the pool's `msg.sender` the router address. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

This creates two mutually exclusive failure modes:

| Router allowlist state | Effect |
|---|---|
| Router **is** allowlisted | Every unprivileged user bypasses the curated allowlist by routing through the router |
| Router **is not** allowlisted | Every individually-allowlisted user is blocked from using the router |

There is no configuration that simultaneously (a) allows allowlisted users to use the router and (b) blocks non-allowlisted users from using the router.

The `DepositAllowlistExtension` checks `owner` (the position owner, not `sender`), so it does not share this flaw for the liquidity path. [4](#0-3) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to KYC'd or otherwise curated addresses is fully bypassed by any unprivileged user who routes through `MetricOmmSimpleRouter`. The user receives oracle-priced output tokens from the pool's reserves without being on the allowlist. This is a direct loss of the pool's curation guarantee and, depending on pool design, a direct loss of LP principal if the pool was intended to trade only with trusted counterparties (e.g., a private market-making pool). Severity: **High**.

---

### Likelihood Explanation

The router is the canonical public entry point for multi-hop swaps. A pool admin who wants allowlisted users to be able to use the router must allowlist the router address — at which point the bypass is immediately available to all users. The trigger requires no special privilege: any user can call `MetricOmmSimpleRouter.exact*` with a target pool that has `SwapAllowlistExtension` configured and the router allowlisted. Likelihood: **High**.

---

### Recommendation

The extension must receive the **original end-user identity**, not the intermediary's address. Two standard approaches:

1. **Pass the original caller through the router.** Have `MetricOmmSimpleRouter` pass the original `msg.sender` as the `recipient` or via `extensionData`, and update `SwapAllowlistExtension` to read the true initiator from `extensionData` rather than from the `sender` argument.

2. **Gate on `recipient` instead of `sender`.** If the pool's swap interface guarantees that `recipient` is always the economic beneficiary, the extension can check `allowedSwapper[pool][recipient]`. This must be verified against the full call path to avoid a different misbinding.

The cleanest fix is option 1: define a standard `extensionData` field that the router always populates with the original `msg.sender`, and have the allowlist extension decode and check that field.

---

### Proof of Concept

```
Setup:
  - Pool P configured with SwapAllowlistExtension (extension1, beforeSwap order = 1)
  - Alice (allowlisted): allowedSwapper[P][alice] = true
  - Bob (NOT allowlisted): allowedSwapper[P][bob] = false
  - Router R is allowlisted so Alice can use it: allowedSwapper[P][R] = true

Attack:
  1. Bob calls MetricOmmSimpleRouter.exactInput(path=[P], ..., recipient=bob)
  2. Router calls P.swap(recipient=bob, ...) — pool's msg.sender = R
  3. Pool calls _beforeSwap(sender=R, recipient=bob, ...)
  4. Extension checks allowedSwapper[P][R] == true  ✓  (passes)
  5. Swap executes; Bob receives oracle-priced token1 from pool reserves

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds — Bob bypassed the curated allowlist
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
