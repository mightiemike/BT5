### Title
SwapAllowlistExtension Checks Router Address as Swapper Identity, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `sender` against `allowedSwapper[pool][sender]`. When a user routes through `MetricOmmSimpleRouter`, the pool's `swap()` is called by the router, so `msg.sender` of `pool.swap()` — and therefore the `sender` the extension sees — is the router address, not the actual end user. If the pool admin allowlists the router (which is required for any allowed user to use the router), every unprivileged user can bypass the allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it to every configured extension:

```solidity
// metric-core/contracts/MetricOmmPool.sol
function swap(...) external whenNotPaused nonReentrant(PoolActions.SWAP) ... {
    ...
    _beforeSwap(
      msg.sender,   // ← direct caller of pool.swap()
      recipient,
      ...
    );
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` then forwards that `sender` value verbatim to every extension in `BEFORE_SWAP_ORDER`:

```solidity
// metric-core/contracts/ExtensionCalling.sol
_callExtensionsInOrder(
  BEFORE_SWAP_ORDER,
  abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, ...)
  )
);
``` [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks that `sender` against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInput(...)`, the router calls `pool.swap()` on the user's behalf. At that point `msg.sender` of `pool.swap()` is the **router address**, not the end user. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][endUser]`.

For any allowlisted user to trade through the router, the pool admin must add the router to the allowlist (`allowedSwapper[pool][router] = true`). Once that entry exists, **every caller of the router** — including completely non-allowlisted addresses — passes the `beforeSwap` check, because the extension cannot distinguish between different users behind the same router address.

The `DepositAllowlistExtension` avoids this problem by checking `owner` (the position recipient, which is user-supplied and pool-enforced) rather than `sender`:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
``` [4](#0-3) 

No equivalent user-identifying field exists on the swap path that the extension could use instead of `sender`.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC'd or otherwise vetted addresses is fully bypassed for any user who routes through `MetricOmmSimpleRouter`. Non-allowlisted users can:

- Execute arbitrage against LP positions, extracting value from LPs who deposited under the assumption that only vetted counterparties would trade against them.
- Drain one-sided bins through repeated directional swaps that the allowlist was intended to prevent.
- Undermine any regulatory or compliance constraint the pool admin intended to enforce.

This constitutes a direct loss of LP principal attributable to unauthorized swap execution — matching the "broken core pool functionality causing loss of funds" and "admin-boundary break bypassed by an unprivileged path" criteria.

---

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is the primary user-facing entrypoint for multi-hop swaps and is expected to be used by the vast majority of traders.
- Any pool admin who wants allowlisted users to be able to use the router **must** add the router to the allowlist, which simultaneously opens the gate to all users.
- No special setup or privileged access is required by the attacker — any EOA or contract can call the router.
- The bypass is deterministic and requires no timing, oracle manipulation, or state precondition.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the actual end user, not the direct caller of `pool.swap()`. Two viable approaches:

1. **Pass the real user through `extensionData`**: Require the router to encode the originating user address in `extensionData` and have the extension decode and check it. This requires the extension to trust that the router populates the field correctly, which introduces a trust assumption on the router.

2. **Check `recipient` instead of `sender`**: If the pool's design guarantees that `recipient` is always the economic beneficiary of the swap, the extension could check `recipient`. However, `recipient` is caller-supplied and may not always equal the end user.

3. **Enforce allowlist at the router level**: The router should verify that `msg.sender` is allowlisted before forwarding to the pool. This keeps the check at the correct trust boundary but requires the router to be allowlist-aware per pool.

The cleanest fix is option 3: add a per-pool allowlist check inside the router's swap path so that the router itself rejects non-allowlisted callers before calling `pool.swap()`.

---

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension in BEFORE_SWAP_ORDER
  allowedUser  = allowedSwapper[pool][allowedUser]  = true
  router       = allowedSwapper[pool][router]        = true  ← required for allowedUser to use router
  attacker     = allowedSwapper[pool][attacker]      = false

Attack:
  attacker calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
    → router calls pool.swap(recipient=attacker, ...)
    → pool passes msg.sender=router as `sender` to _beforeSwap
    → SwapAllowlistExtension.beforeSwap receives sender=router
    → allowedSwapper[pool][router] == true → check passes
    → swap executes, attacker receives output tokens
    → LP funds reduced by the swap delta

Result:
  attacker successfully swaps on a pool they are not allowlisted for,
  bypassing the curated-pool protection entirely.
``` [3](#0-2) [1](#0-0) [2](#0-1)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
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
