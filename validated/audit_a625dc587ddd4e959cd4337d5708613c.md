### Title
Native ETH Permanently Locked in `DirectDepositV1` — Recovery Path in `ContractOwner` Always Reverts — (`core/contracts/ContractOwner.sol`, `core/contracts/DirectDepositV1.sol`)

---

### Summary

`ContractOwner` has no `receive()` or `fallback()` function. The only recovery path for native ETH held in a `DirectDepositV1` contract — `withdrawFromDirectDepositV1(subaccount, address(0))` — calls `DirectDepositV1.withdrawNative()`, which attempts to push ETH to `msg.sender` (i.e., `ContractOwner`). Because `ContractOwner` cannot accept ETH, this call always reverts. Any native ETH that ends up in a `DirectDepositV1` instance is permanently locked with no recovery path.

---

### Finding Description

`DirectDepositV1` has two code paths through which native ETH can accumulate without being wrapped:

**Path 1 — Constructor silent failure:** [1](#0-0) 

If ETH is present at construction time and the `wrappedNative.call{value: balance}("")` fails, the constructor emits `NativeTokenTransferFailed` and continues without reverting. The ETH remains in the contract.

**Path 2 — `selfdestruct` force-send:**

`DirectDepositV1.receive()` requires the wrapping call to succeed or it reverts. However, ETH sent via `selfdestruct` from another contract bypasses `receive()` entirely, depositing ETH directly into the contract balance with no wrapping. [2](#0-1) 

In both cases, the intended recovery function is `ContractOwner.withdrawFromDirectDepositV1`: [3](#0-2) 

This calls `DirectDepositV1.withdrawNative()`: [4](#0-3) 

`withdrawNative()` executes `msg.sender.call{value: balance}("")` where `msg.sender` is `ContractOwner`. `ContractOwner` inherits only from `EIP712Upgradeable` and `OwnableUpgradeable` — neither provides a `receive()` or `fallback()` function, and `ContractOwner` itself defines none: [5](#0-4) 

The low-level call therefore returns `success = false`, `withdrawNative()` reverts with `"Failed to transfer native token to owner"`, and the entire `withdrawFromDirectDepositV1` call reverts. There is no alternative ETH recovery path anywhere in the codebase.

---

### Impact Explanation

Native ETH that accumulates in any `DirectDepositV1` instance — whether from a constructor-time wrapping failure or a `selfdestruct` force-send — is permanently irrecoverable. The only designated recovery function (`withdrawFromDirectDepositV1` with `token == address(0)`) is unconditionally broken. The ETH balance is frozen in the `DirectDepositV1` contract forever.

---

### Likelihood Explanation

The `selfdestruct` vector is directly user-triggerable by any unprivileged actor: deploy a contract funded with ETH, call `selfdestruct(directDepositV1Address)`, and ETH is force-deposited into `DirectDepositV1` with no wrapping. The constructor failure path is lower likelihood but is explicitly anticipated by the codebase (the `NativeTokenTransferFailed` event exists precisely for this case), confirming the developers expected this scenario. In both cases the recovery mechanism is provably broken.

---

### Recommendation

Add a `receive()` function to `ContractOwner` so it can accept ETH forwarded from `DirectDepositV1.withdrawNative()`:

```solidity
receive() external payable {}
```

Alternatively, refactor `DirectDepositV1.withdrawNative()` to accept an explicit `address payable recipient` parameter and pass `msg.sender` (the EOA owner) directly, bypassing `ContractOwner` as an intermediary ETH recipient.

---

### Proof of Concept

1. Deploy a `DirectDepositV1` instance via `ContractOwner.createDirectDepositV1(subaccount)`.
2. Deploy an attacker contract holding 1 wei and call `selfdestruct(directDepositV1Address)`. ETH is force-deposited; `receive()` is not invoked.
3. Confirm `address(directDepositV1).balance == 1`.
4. Call `ContractOwner.withdrawFromDirectDepositV1(subaccount, address(0))` as the multisig owner.
5. Observe revert: `DirectDepositV1.withdrawNative()` calls `ContractOwner.call{value: 1}("")` → `ContractOwner` has no `receive()` → `success = false` → reverts with `"Failed to transfer native token to owner"`.
6. ETH remains locked in `DirectDepositV1` with no further recovery path. [4](#0-3) [6](#0-5)

### Citations

**File:** core/contracts/DirectDepositV1.sol (L52-60)
```text
        uint256 balance = address(this).balance;
        if (balance != 0) {
            // shouldn't revert even if the transfer fails, otherwise the funds
            // will be stuck in the DDA forever.
            (bool success, ) = wrappedNative.call{value: balance}("");
            if (!success) {
                emit NativeTokenTransferFailed(balance);
            }
        }
```

**File:** core/contracts/DirectDepositV1.sol (L64-67)
```text
    receive() external payable {
        (bool success, ) = wrappedNative.call{value: msg.value}("");
        require(success, "Failed to wrap native token.");
    }
```

**File:** core/contracts/DirectDepositV1.sol (L108-112)
```text
    function withdrawNative() external onlyOwner {
        uint256 balance = address(this).balance;
        (bool success, ) = msg.sender.call{value: balance}("");
        require(success, "Failed to transfer native token to owner");
    }
```

**File:** core/contracts/ContractOwner.sol (L21-46)
```text
contract ContractOwner is EIP712Upgradeable, OwnableUpgradeable {
    error InvalidInput();
    using MathSD21x18 for int128;
    using ERC20Helper for IERC20Base;

    address internal deployer;
    SpotEngine internal spotEngine;
    PerpEngine internal perpEngine;
    Endpoint internal endpoint;
    IClearinghouse internal clearinghouse;
    Verifier internal verifier;
    address payable internal wrappedNative;

    bytes[] internal updateProductTxs; // deprecated
    bytes[] internal rawSpotAddProductCalls; // deprecated
    bytes[] internal rawPerpAddProductCalls; // deprecated

    mapping(bytes32 => address payable) public directDepositV1Address;

    bytes[] internal rawSpotAddOrUpdateProductCalls;
    bytes[] internal rawPerpAddOrUpdateProductCalls;

    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
    }
```

**File:** core/contracts/ContractOwner.sol (L622-637)
```text
    function withdrawFromDirectDepositV1(bytes32 subaccount, address token)
        external
        onlyOwner
    {
        address payable directDepositV1 = directDepositV1Address[subaccount];
        require(directDepositV1 != address(0), "no dda");
        if (token == address(0)) {
            uint256 preBalance = address(this).balance;
            DirectDepositV1(directDepositV1).withdrawNative();
            uint256 postBalance = address(this).balance;
            require(postBalance > preBalance, "empty");
            (bool success, ) = msg.sender.call{value: postBalance - preBalance}(
                ""
            );
            require(success, "xfer");
        } else {
```
