### Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Any Address to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

The `DepositAllowlistExtension` is documented as gating `addLiquidity` by the **depositor** address. However, its `beforeAddLiquidity` hook silently ignores the `sender` parameter (the actual caller who provides tokens via callback) and instead checks the `owner` parameter (the position recipient). Any unprivileged address can bypass the allowlist by calling `pool.addLiquidity(allowlistedAddress, ...)`, satisfying the guard while the real depositor is never checked.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct addresses into the extension hook:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

`msg.sender` is the caller who will supply tokens through the swap callback; `owner` is the address that receives the LP position. The `IMetricOmmExtensions` interface names these parameters explicitly:

```solidity
function beforeAddLiquidity(
    address sender,
    address owner,
    ...
) external returns (bytes4);
``` [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` drops `sender` (unnamed first argument) and gates only on `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [3](#0-2) 

The contract's own NatSpec states: *"Gates `addLiquidity` by depositor address, per pool"* and the mapping is named `allowedDepositor`. The intent is unambiguous — gate the caller — but the implementation gates the position recipient.

Compare with `SwapAllowlistExtension`, which correctly checks `sender` (the swap caller):

```solidity
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
``` [4](#0-3) 

The asymmetry confirms the deposit extension checks the wrong address.

---

### Impact Explanation

A pool admin deploys `DepositAllowlistExtension` to restrict liquidity provision to a curated set of addresses (e.g., KYC-verified LPs, institutional partners, or whitelisted market makers). Because the guard checks `owner` rather than `sender`, any unprivileged address can:

1. Call `pool.addLiquidity(allowlistedAddress, salt, deltas, callbackData, extensionData)` with an allowlisted `owner`.
2. The extension passes (`allowedDepositor[pool][allowlistedAddress] == true`).
3. The caller provides tokens via the `IMetricOmmAddLiquidityCallback` callback.
4. The position is credited to the allowlisted `owner`, but the actual token deposit was performed by the unauthorized `sender`.

The pool admin's access-control boundary is fully bypassed by an unprivileged path. This breaks the admin-boundary invariant: an unprivileged address performs an action the pool admin explicitly restricted. Downstream consequences include unauthorized addresses influencing pool bin state, earning spread fees through positions they should not hold, and violating any compliance or operational constraints the allowlist was meant to enforce.

---

### Likelihood Explanation

Exploitation requires no special privileges, no flash loan, and no oracle manipulation. Any EOA or contract can call `addLiquidity` with a known allowlisted address as `owner`. The allowlisted address is discoverable on-chain via the public `allowedDepositor` mapping. Likelihood is **High**.

---

### Recommendation

Change `beforeAddLiquidity` to check `sender` (the actual caller) instead of `owner`:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

This mirrors the correct pattern already used in `SwapAllowlistExtension`.

---

### Proof of Concept

```
Setup:
  - Pool P is deployed with DepositAllowlistExtension E as a beforeAddLiquidity hook.
  - Pool admin allowlists Alice: allowedDepositor[P][Alice] = true.
  - Bob is NOT allowlisted: allowedDepositor[P][Bob] = false.

Attack:
  1. Bob calls P.addLiquidity(owner=Alice, salt, deltas, callbackData, extensionData).
  2. Pool calls E.beforeAddLiquidity(sender=Bob, owner=Alice, ...).
  3. Extension checks allowedDepositor[P][Alice] == true → passes, no revert.
  4. Pool calls Bob's callback; Bob transfers tokens into the pool.
  5. Alice receives the LP position shares.

Result:
  - Bob, an address the pool admin explicitly excluded, has successfully added
    liquidity to the restricted pool.
  - The deposit allowlist guard is fully bypassed.
  - The pool admin's access-control invariant is broken.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L31-38)
```text

  function beforeRemoveLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) external returns (bytes4);
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
