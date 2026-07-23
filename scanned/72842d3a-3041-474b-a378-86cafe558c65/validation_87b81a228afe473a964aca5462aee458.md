### Title
`DepositAllowlistExtension` checks position `owner` instead of transaction `sender`, allowing any caller to bypass the deposit allowlist - (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual `msg.sender` of `addLiquidity`) and instead checks the caller-supplied `owner` parameter against the allowlist. Because `MetricOmmPool.addLiquidity` lets any caller nominate an arbitrary `owner`, any non-allowlisted address can bypass the deposit gate by setting `owner` to an already-allowlisted address.

### Finding Description

`MetricOmmPool.addLiquidity` forwards two distinct addresses into the extension hook:

```solidity
// MetricOmmPool.sol
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` encodes both and passes them to the extension:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
``` [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first argument but names it `address` (anonymous, discarded) and gates on `owner` instead:

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

The public interface names the second parameter `depositor`, confirming the design intent is to gate the depositing actor, not the position owner:

```solidity
function isAllowedToDeposit(address pool_, address depositor) external view returns (bool);
``` [4](#0-3) 

**Attack path:**

1. Pool `P` has `DepositAllowlistExtension` with `allowedDepositor[P][alice] = true`, `allowedDepositor[P][bob] = false`.
2. Bob (non-allowlisted) calls `P.addLiquidity(alice, salt, deltas, callbackData, extensionData)`.
3. The extension evaluates `allowedDepositor[P][alice]` → `true` → hook passes.
4. `LiquidityLib.addLiquidity` executes: Bob's tokens are pulled from Bob via the `IMetricOmmModifyLiquidityCallback`, and the position is recorded under `owner = alice`.
5. Alice calls `P.removeLiquidity(alice, salt, deltas, extensionData)` and withdraws Bob's tokens.

The `removeLiquidity` path has no allowlist hook, so Alice's withdrawal is unconditional:

```solidity
if (msg.sender != owner) revert NotPositionOwner();
``` [5](#0-4) 

### Impact Explanation

The deposit allowlist — the pool admin's primary access-control guard over who may provide liquidity — is fully bypassed by any unprivileged caller. Consequences include:

- **Admin-boundary break**: a pool configured to accept liquidity only from KYC'd, whitelisted, or otherwise vetted addresses can be deposited into by anyone.
- **Value transfer**: the attacker's tokens enter the pool and are claimable by the allowlisted `owner`, constituting a forced, unrecoverable transfer of the attacker's principal to a third party.
- **Pool-state manipulation**: a non-allowlisted actor can shift bin positions or pool composition in a restricted pool, undermining the LP risk model the allowlist was meant to enforce.

### Likelihood Explanation

Exploitation requires no privileges, no special tokens, and no multi-step setup. A single `addLiquidity` call with `owner` set to any known allowlisted address is sufficient. The allowlisted addresses are discoverable on-chain via `AllowedToDepositSet` events or `allowedDepositor` reads.

### Recommendation

Gate on `sender` (the actual depositor) rather than `owner` (the position recipient):

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [3](#0-2) 

This aligns the implementation with the `isAllowedToDeposit(pool, depositor)` interface contract and closes the bypass.

### Proof of Concept

```
Setup:
  pool P  →  DepositAllowlistExtension
  allowedDepositor[P][alice] = true
  allowedDepositor[P][bob]   = false   (bob is the attacker)

Step 1 – Bob calls:
  P.addLiquidity(
      owner        = alice,
      salt         = 0,
      deltas       = { binIdxs: [0], shares: [1e18] },
      callbackData = <bob pays tokens in callback>,
      extensionData = ""
  )

Step 2 – Extension check:
  allowedDepositor[P][alice] == true  →  hook passes

Step 3 – LiquidityLib pulls tokens from Bob via metricOmmModifyLiquidityCallback.
         Position (alice, 0) is minted with 1e18 shares.

Step 4 – Alice calls:
  P.removeLiquidity(alice, 0, { binIdxs:[0], shares:[1e18] }, "")
  msg.sender == owner == alice  →  passes
  Bob's tokens are returned to Alice.

Result: Bob's tokens transferred to Alice; deposit allowlist fully bypassed.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
```

**File:** metric-core/contracts/ExtensionCalling.sol (L95-98)
```text
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L28-30)
```text
  function isAllowedToDeposit(address pool_, address depositor) external view returns (bool) {
    return allowAllDepositors[pool_] || allowedDepositor[pool_][depositor];
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
