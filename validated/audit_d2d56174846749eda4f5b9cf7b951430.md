### Title
`SwapAllowlistExtension` gates on `sender` (router) instead of `recipient` (economic actor), allowing any user to bypass the swap allowlist via an allowlisted router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension::beforeSwap` checks the `sender` parameter (the direct caller of `pool.swap`, i.e., the router) against the per-pool allowlist. The actual economic actor — the `recipient` who receives the output tokens — is silently ignored. Any unprivileged user can bypass the swap allowlist by routing through any allowlisted router contract. This is structurally identical to the external bug: the wrong identity is checked, so the guard is misapplied.

---

### Finding Description

In `MetricOmmPool.swap`, the pool passes `msg.sender` as `sender` to the extension hook:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // sender = the direct caller (router)
    recipient,    // recipient = who receives output tokens
    ...
);
```

`ExtensionCalling._beforeSwap` forwards both values verbatim to every configured extension:

```solidity
// ExtensionCalling.sol line 160-176
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then checks only `sender` (the router) and silently discards `recipient` (the user):

```solidity
// SwapAllowlistExtension.sol line 31-41
function beforeSwap(address sender, address, ...)   // recipient unnamed/ignored
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Compare with `DepositAllowlistExtension.beforeAddLiquidity`, which correctly ignores `sender` (the router) and checks `owner` (the LP position owner — the economic actor):

```solidity
// DepositAllowlistExtension.sol line 32-42
function beforeAddLiquidity(address, address owner, ...)  // sender unnamed/ignored
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

The two sibling extensions apply opposite identity semantics:

| Extension | Checked identity | Ignored identity |
|---|---|---|
| `DepositAllowlistExtension` | `owner` (the user / economic actor) | `sender` (the router) |
| `SwapAllowlistExtension` | `sender` (the router) | `recipient` (the user / economic actor) |

**Bypass path:** A pool admin deploys a pool with `SwapAllowlistExtension` intending to restrict swaps to specific users. They allowlist `MetricOmmSimpleRouter` (or any public router) so that allowlisted users can swap conveniently. Because the extension checks the router address, every user of that router — including completely non-allowlisted users — passes the guard. The allowlist is fully neutralized.

**Blocked-valid-actor path (direct analog to the external bug):** A pool admin allowlists specific user addresses in `SwapAllowlistExtension` (reasoning by analogy with `DepositAllowlistExtension`, which checks the user). When those users call through any router, the router's address is checked, not theirs. The allowlisted users are blocked from swapping through any router, even though they are individually permitted.

---

### Impact Explanation

The swap allowlist guard is the primary access-control mechanism for permissioned pools. Its bypass allows:

- Non-allowlisted users to execute swaps in pools intended to be KYC/whitelist-restricted, draining LP liquidity at oracle prices.
- Pool admins who configure user-level allowlists (by analogy with `DepositAllowlistExtension`) to unknowingly leave the guard completely open to any user of an allowlisted router.

This is a broken core pool invariant: the configured guard does not restrict the intended actor class, causing the pool to behave as if `allowAllSwappers = true` for any user of an allowlisted router.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the standard periphery entry point for swaps. Any pool that allowlists it (the expected production configuration) exposes the bypass to all users.
- The inconsistency with `DepositAllowlistExtension` makes it highly likely that pool admins will configure `SwapAllowlistExtension` with user addresses, triggering the blocked-valid-actor path.
- No privileged access or special setup is required; any user can call the router.

---

### Recommendation

Change `SwapAllowlistExtension::beforeSwap` to check `recipient` (the economic beneficiary of the swap) instead of `sender` (the router), making it consistent with `DepositAllowlistExtension`:

```solidity
// SwapAllowlistExtension.sol
function beforeSwap(address, address recipient, ...)   // sender ignored, recipient checked
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][recipient]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Update `setAllowedToSwap`, `isAllowedToSwap`, and the `AllowedToSwapSet` event to document that the checked address is the swap recipient, not the caller.

---

### Proof of Concept

1. Pool is deployed with `SwapAllowlistExtension` configured.
2. Admin calls `setAllowedToSwap(pool, routerAddress, true)` to allowlist `MetricOmmSimpleRouter`.
3. Non-allowlisted user `U` calls `MetricOmmSimpleRouter.swap(pool, recipient=U, ...)`.
4. Router calls `pool.swap(recipient=U, ...)` with `msg.sender = routerAddress`.
5. Pool calls `_beforeSwap(sender=routerAddress, recipient=U, ...)`.
6. `SwapAllowlistExtension` evaluates `allowedSwapper[pool][routerAddress]` → `true`.
7. Swap executes. User `U` receives output tokens despite never being allowlisted.

Alternatively (blocked-valid-actor path):

1. Admin calls `setAllowedToSwap(pool, userAddress, true)` intending to allowlist user `U`.
2. User `U` calls `MetricOmmSimpleRouter.swap(pool, recipient=U, ...)`.
3. Router calls `pool.swap(recipient=U, ...)` with `msg.sender = routerAddress`.
4. `SwapAllowlistExtension` evaluates `allowedSwapper[pool][routerAddress]` → `false`.
5. Revert: `NotAllowedToSwap()`. User `U` is blocked despite being individually allowlisted. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
