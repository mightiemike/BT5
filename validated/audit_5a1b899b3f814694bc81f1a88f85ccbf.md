### Title
`multisig` Owner Address Accepted Without Smart Contract Validation at Initialization — (`File: core/contracts/ContractOwner.sol`)

---

### Summary
`ContractOwner.initialize()` accepts a parameter explicitly named `multisig` and immediately transfers protocol ownership to it via `transferOwnership(multisig)` with no on-chain check that the address is a deployed smart contract. If an EOA is supplied, the entire protocol admin surface is controlled by a single private key.

---

### Finding Description
In `ContractOwner.sol`, the `initialize()` function receives a `multisig` address and unconditionally transfers `OwnableUpgradeable` ownership to it:

```solidity
function initialize(
    address multisig,
    address _deployer,
    ...
) external initializer {
    require(_deployer == msg.sender, "expected deployed to initialize");
    __Ownable_init();
    transferOwnership(multisig);   // ← no extcodesize / isContract check
    ...
}
```

The parameter name `multisig` signals the protocol's own intent that this address must be a multisig contract, yet no `extcodesize` check, `Address.isContract()` guard, or similar validation is present. Any plain EOA address is silently accepted and becomes the sole owner. [1](#0-0) 

---

### Impact Explanation
`ContractOwner` is the single administrative hub for the entire Nado protocol. Its `onlyOwner`-gated functions include:

- `addEngine` — registers new spot/perp engines into the clearinghouse
- `assignPubKey` / `deletePubkey` — controls the sequencer verifier key set
- `withdrawInsurance` — drains the protocol insurance fund to an arbitrary `sendTo`
- `spotUpdateRisk` / `perpUpdateRisk` — rewrites all collateral risk parameters
- `updateTierFeeRates` — manipulates maker/taker fee rates for all products [2](#0-1) 

If `multisig` is an EOA, a single private-key compromise gives an attacker unrestricted access to all of the above. An attacker could drain the insurance fund, corrupt risk weights to enable under-collateralised positions, or inject a malicious engine to steal deposited collateral.

---

### Likelihood Explanation
The risk is **medium**. The parameter name `multisig` creates a false sense of security — a deployer may pass a hot-wallet address during testing or a rushed deployment and never rotate it. There is no on-chain enforcement preventing this, and the contract provides no post-deployment mechanism to detect or remediate the misconfiguration. Once ownership is transferred to an EOA, the entire protocol's security reduces to the secrecy of one private key.

---

### Recommendation
Add an `extcodesize` guard before `transferOwnership`:

```solidity
require(_isContract(multisig), "multisig must be a contract");
transferOwnership(multisig);
```

where `_isContract` checks `extcodesize(multisig) > 0`. Alternatively, use OpenZeppelin's `Address.isContract()`. This mirrors the recommendation in the external report: the privileged address that receives total protocol control must be verifiably a smart contract (DAO or multisig), not an EOA. [3](#0-2) 

---

### Proof of Concept

1. Deployer calls `ContractOwner.initialize(EOA_ADDRESS, deployer, ...)` where `EOA_ADDRESS` is a plain wallet.
2. `transferOwnership(EOA_ADDRESS)` succeeds — no revert, no event distinguishing EOA from contract.
3. Attacker compromises `EOA_ADDRESS` private key.
4. Attacker calls `ContractOwner.withdrawInsurance(totalInsurance, attacker)` — passes `onlyOwner`, submits a slow-mode `WithdrawInsurance` transaction, drains the insurance fund to the attacker's address.
5. Attacker calls `assignPubKey(i, maliciousX, maliciousY)` — replaces a sequencer verifier key, enabling forged transaction signatures.

All steps are reachable by an unprivileged external caller once the EOA key is compromised, with no further preconditions. [4](#0-3) [5](#0-4)

### Citations

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

**File:** core/contracts/ContractOwner.sol (L235-247)
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
```

**File:** core/contracts/ContractOwner.sol (L433-465)
```text
    function addEngine(
        address engine,
        address offchainExchange,
        IProductEngine.EngineType engineType
    ) external onlyOwner {
        clearinghouse.addEngine(engine, offchainExchange, engineType);
    }

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
