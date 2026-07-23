### Title
`SwapAllowlistExtension` gates the router address instead of the actual swapper, allowing non-allowlisted users to bypass the per-pool swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router address, not the actual user. If the pool admin allowlists the router so that allowlisted users can reach the pool through the supported periphery path, every unpermissioned user gains the same access by routing through the same public contract.

---

### Finding Description

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()`: [3](#0-2) 

When a user goes through `MetricOmmSimpleRouter`, the router is the direct caller of `pool.swap()`, so `sender = router_address`. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

The pool admin faces an inescapable dilemma:

| Admin choice | Effect on allowlisted users | Effect on non-allowlisted users |
|---|---|---|
| Do **not** allowlist the router | Cannot use the router (broken) | Correctly blocked |
| **Allowlist the router** | Can use the router | **Also pass — full bypass** |

There is no configuration that simultaneously lets allowlisted users reach the pool through the supported router and blocks non-allowlisted users from doing the same. The contract's own NatSpec declares the intent as "Gates `swap` by swapper address, per pool", but the implementation gates the intermediary contract address instead. [4](#0-3) 

---

### Impact Explanation

A curated pool (e.g., KYC-only, institutional, or regulatory-restricted) that deploys `SwapAllowlistExtension` to enforce a per-user allowlist can have that allowlist completely bypassed. Any unpermissioned user calls the public `MetricOmmSimpleRouter`, which calls `pool.swap()` with itself as `msg.sender`. If the router is allowlisted (the only way to let legitimate users use the router), the extension returns success for every caller regardless of their identity. Non-allowlisted users receive pool output tokens and the pool receives their input tokens — a direct, fund-impacting policy violation on every such swap.

---

### Likelihood Explanation

The bypass requires the pool admin to allowlist the router, which is the natural operational step any admin would take when deploying a curated pool alongside the supported periphery. The router is a public, permissionless contract. Once the router is allowlisted, the bypass is available to any address with no further preconditions, no special tokens, and no privileged access.

---

### Recommendation

The extension must gate the economically relevant actor, not the intermediary. Two sound approaches:

1. **Check `recipient` instead of `sender`**: The output-token recipient is the economic beneficiary of the swap and is always explicitly set by the originating user. Changing the check to `allowedSwapper[pool][recipient]` correctly identifies the user regardless of routing path.

2. **Decode identity from `extensionData`**: Require the router to embed the originating user's address in `extensionData` and verify it with a signature or trusted-forwarder pattern inside the extension. This is more flexible but requires router cooperation.

Option 1 is the minimal, non-breaking fix: replace `sender` with the `recipient` parameter (second positional argument) in `beforeSwap`.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  admin: setAllowedToSwap(pool, alice, true)          // allowlist alice
  admin: setAllowedToSwap(pool, router, true)          // allow alice to use the router
  bob is NOT allowlisted

Attack:
  bob calls MetricOmmSimpleRouter.exactInput(pool, ...)
    → router calls pool.swap(bob_recipient, ...)
    → pool calls extension.beforeSwap(router_address, bob_recipient, ...)
    → extension checks: allowedSwapper[pool][router_address] == true  ✓
    → swap executes; bob receives output tokens

Result:
  bob, a non-allowlisted user, successfully swaps on a curated pool.
  The allowlist is completely bypassed for every user who routes through the public router.
``` [3](#0-2) [1](#0-0)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-13)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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
