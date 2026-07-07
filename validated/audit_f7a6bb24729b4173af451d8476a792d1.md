### Title
`ContractOwner` Uses Single-Step `OwnableUpgradeable` Instead of Two-Step `Ownable2StepUpgradeable`, Removing Ownership-Transfer Safeguard - (File: `core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.sol` inherits from `OwnableUpgradeable` and immediately transfers ownership to a caller-supplied `multisig` address in `initialize` via a single-step `transferOwnership`. No two-step (`Ownable2StepUpgradeable`) mechanism exists anywhere in the codebase. If the `multisig` address is incorrect (typo, incompatible contract, uninitialized multisig), ownership is irrecoverably lost in the same transaction, permanently bricking all `onlyOwner`-gated protocol administration.

---

### Finding Description

`ContractOwner` is the central administrative contract for the Nado protocol. It controls verifier public key rotation, risk parameter updates, insurance fund management, NLP pool lifecycle, product listing, fee rate configuration, engine registration, and withdraw pool assignment — all gated by `onlyOwner`. [1](#0-0) 

The `initialize` function calls `__Ownable_init()` and then immediately calls `transferOwnership(multisig)`: [2](#0-1) 

`OwnableUpgradeable.transferOwnership` is a **single-step** operation: it sets `_owner = multisig` atomically with no pending-owner confirmation step. A grep across the entire codebase for `Ownable2Step`, `Ownable2StepUpgradeable`, `pendingOwner`, and `acceptOwnership` returns **zero matches** — confirming no two-step safeguard exists anywhere.

The same pattern appears in `BaseEngine._initialize`, which calls `transferOwnership(_admin)` for both `SpotEngine` and `PerpEngine`: [3](#0-2) 

---

### Impact Explanation

If `multisig` (passed to `ContractOwner.initialize`) or `_admin` (passed to `BaseEngine._initialize`) is an incorrect address — due to a deployment script typo, an uninitialized multisig contract, or an incompatible proxy — ownership is immediately and permanently transferred to an inoperable address. All of the following `onlyOwner` functions in `ContractOwner` become permanently inaccessible:

- `assignPubKey` / `deletePubkey` → verifier key rotation is frozen; compromised sequencer keys cannot be revoked [4](#0-3) 
- `spotUpdateRisk` / `perpUpdateRisk` → risk parameters cannot be updated [5](#0-4) 
- `withdrawInsurance` / `depositInsurance` → insurance fund is frozen [6](#0-5) 
- `addOrUpdateProducts`, `updateTierFeeRates`, `addEngine`, `setWithdrawPool`, `setSpreads` → protocol configuration is permanently frozen [7](#0-6) 

For `BaseEngine`, loss of owner on `SpotEngine`/`PerpEngine` means `updateRisk` is permanently inaccessible, freezing all collateral weight and price parameters.

---

### Likelihood Explanation

Likelihood is low under normal operations but non-negligible at deployment time. The `multisig` address is passed as a plain `address` parameter with no on-chain validation that the target can accept ownership. A deployment script error, an undeployed multisig address, or a wrong network address would silently succeed and permanently lock the protocol. The perceived safety of "we're deploying to a multisig" masks the absence of any confirmation step.

---

### Recommendation

Replace `OwnableUpgradeable` with `Ownable2StepUpgradeable` in `ContractOwner`, `BaseEngine` (and by extension `SpotEngine`, `PerpEngine`), `Endpoint`, `Verifier`, `Airdrop`, and `BaseProxyManager`. With `Ownable2StepUpgradeable`, `transferOwnership` only sets a `pendingOwner`; the new owner must call `acceptOwnership` to complete the transfer. This ensures that if the target address is inoperable, the current owner retains control.

```solidity
// ContractOwner.sol
- import "@openzeppelin/contracts-upgradeable/access/OwnableUpgradeable.sol";
+ import "@openzeppelin/contracts-upgradeable/access/Ownable2StepUpgradeable.sol";

- contract ContractOwner is EIP712Upgradeable, OwnableUpgradeable {
+ contract ContractOwner is EIP712Upgradeable, Ownable2StepUpgradeable {
```

---

### Proof of Concept

1. Deployer calls `ContractOwner.initialize(multisig, ...)` where `multisig` is a mistyped address or an undeployed contract.
2. `__Ownable_init()` sets `_owner = deployer`; `transferOwnership(multisig)` immediately sets `_owner = multisig` in the same call.
3. The deployer no longer has any ownership claim.
4. `multisig` cannot call `acceptOwnership` (it doesn't exist in `OwnableUpgradeable`) and cannot execute any transactions.
5. All `onlyOwner` functions — including `assignPubKey`, `spotUpdateRisk`, `withdrawInsurance`, `addOrUpdateProducts` — revert permanently.
6. The protocol's administrative layer is bricked with no recovery path. [8](#0-7)

### Citations

**File:** core/contracts/ContractOwner.sol (L21-21)
```text
contract ContractOwner is EIP712Upgradeable, OwnableUpgradeable {
```

**File:** core/contracts/ContractOwner.sol (L48-68)
```text
    function initialize(
        address multisig,
        address _deployer,
        address _spotEngine,
        address _perpEngine,
        address _endpoint,
        address _clearinghouse,
        address _verifier,
        address payable _wrappedNative
    ) external initializer {
        require(_deployer == msg.sender, "expected deployed to initialize");
        __Ownable_init();
        transferOwnership(multisig);
        deployer = _deployer;
        spotEngine = SpotEngine(_spotEngine);
        perpEngine = PerpEngine(_perpEngine);
        endpoint = Endpoint(_endpoint);
        clearinghouse = IClearinghouse(_clearinghouse);
        verifier = Verifier(_verifier);
        wrappedNative = _wrappedNative;
    }
```

**File:** core/contracts/ContractOwner.sol (L147-150)
```text
    function addOrUpdateProducts(
        uint32[] memory spotIds,
        uint32[] memory perpIds
    ) external onlyOwner {
```

**File:** core/contracts/ContractOwner.sol (L235-263)
```text
    function withdrawInsurance(uint128 amount, address sendTo)
        external
        onlyOwner
    {
        IEndpoint.WithdrawInsurance memory _txn = IEndpoint.WithdrawInsurance(
            amount,
            sendTo
        );
        _submitSlowModeTransaction(
            IEndpoint.TransactionType.WithdrawInsurance,
            abi.encode(_txn)
        );
    }

    function depositInsurance(uint128 amount) external onlyOwner {
        IERC20Base quoteToken = IERC20Base(
            spotEngine.getToken(QUOTE_PRODUCT_ID)
        );

        quoteToken.approve(address(endpoint), uint256(amount));

        IEndpoint.DepositInsurance memory _txn = IEndpoint.DepositInsurance(
            amount
        );
        _submitSlowModeTransaction(
            IEndpoint.TransactionType.DepositInsurance,
            abi.encode(_txn)
        );
    }
```

**File:** core/contracts/ContractOwner.sol (L441-451)
```text
    function assignPubKey(
        uint256 i,
        uint256 x,
        uint256 y
    ) public onlyOwner {
        verifier.assignPubKey(i, x, y);
    }

    function deletePubkey(uint256 index) public onlyOwner {
        verifier.deletePubkey(index);
    }
```

**File:** core/contracts/ContractOwner.sol (L453-465)
```text
    function spotUpdateRisk(
        uint32 productId,
        RiskHelper.RiskStore memory riskStore
    ) external onlyOwner {
        spotEngine.updateRisk(productId, riskStore);
    }

    function perpUpdateRisk(
        uint32 productId,
        RiskHelper.RiskStore memory riskStore
    ) external onlyOwner {
        perpEngine.updateRisk(productId, riskStore);
    }
```

**File:** core/contracts/BaseEngine.sol (L208-212)
```text
    ) internal initializer {
        __Ownable_init();
        setEndpoint(_endpointAddr);
        transferOwnership(_admin);

```
