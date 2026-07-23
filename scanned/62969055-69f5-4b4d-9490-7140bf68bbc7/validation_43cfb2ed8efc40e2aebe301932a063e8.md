### Title
`SwapAllowlistExtension.beforeSwap` Checks the Router Address Instead of the Actual User, Allowing Any User to Bypass the Swap Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension` is designed to gate swaps on a curated pool to a specific set of approved addresses. Its `beforeSwap` hook checks the `sender` parameter, which is `msg.sender` of the pool's `swap()` call. When users route through `MetricOmmSimpleRouter`, `sender` is the router contract, not the actual user. If the pool admin allowlists the router (which is required for any allowlisted user to use the router), every user — including those not on the allowlist — can bypass the guard by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

The pool passes `msg.sender` of the `swap()` call as `sender` to every extension hook:

```solidity
_beforeSwap(
    msg.sender,   // ← this is the router, not the end user
    recipient,
    ...
);
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards this value verbatim:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
``` [3](#0-2) 

The pool admin faces an inescapable dilemma:

| Admin choice | Effect |
|---|---|
| Allowlist only specific user addresses (not the router) | Allowlisted users cannot use the router at all |
| Allowlist the router | Every user on-chain can bypass the allowlist via the router |

There is no configuration that achieves "allow specific users to swap through the router." The extension checks the wrong actor.

Contrast this with `DepositAllowlistExtension`, which correctly checks `owner` (the actual beneficiary) rather than `sender` (the intermediary adder contract):

```solidity
function beforeAddLiquidity(address, address owner, ...)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
``` [4](#0-3) 

The asymmetry is structural: the deposit extension gates on the economic beneficiary (`owner`); the swap extension gates on the intermediary (`sender`/router).

---

### Impact Explanation

Any user can swap on a pool configured as curated/restricted by routing through `MetricOmmSimpleRouter`. The pool admin's allowlist policy is silently nullified. Unauthorized counterparties can trade against LP positions, exposing LPs to adverse selection from actors the pool was explicitly designed to exclude. This breaks the core pool protection mechanism and constitutes a broken-invariant loss path for LP depositors.

---

### Likelihood Explanation

High. The router is the primary user-facing entry point documented by the protocol. Any pool admin who deploys a curated pool and also wants users to use the router must allowlist the router, at which point the allowlist is fully bypassed. The attacker needs no special privilege — only the ability to call `MetricOmmSimpleRouter`.

---

### Recommendation

Gate on the actual user identity rather than the intermediary. Two options:

1. **Check `recipient` instead of `sender`** in `beforeSwap` — `recipient` is the address that receives output tokens and is set by the end user, not the router. This is the minimal change and mirrors how `DepositAllowlistExtension` uses `owner`.

2. **Pass the real user address through `extensionData`** — require the router to encode the originating user in `extensionData` and have the extension decode and check it. This is more robust but requires router cooperation.

Option 1 is the direct analog of the deposit extension's correct pattern.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured. Only `alice` is allowlisted: `allowedSwapper[pool][alice] = true`.
2. Pool admin also allowlists the router so that `alice` can use it: `allowedSwapper[pool][router] = true`.
3. `bob` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`.
4. The router calls `pool.swap(recipient=bob, ...)` with `msg.sender = router`.
5. `beforeSwap` receives `sender = router`. The check `allowedSwapper[pool][router]` is `true` → no revert.
6. `bob`'s swap executes against LP positions despite `bob` never being allowlisted. [1](#0-0) [2](#0-1)

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
