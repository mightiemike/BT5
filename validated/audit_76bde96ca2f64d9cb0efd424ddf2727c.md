### Title
Mistakenly Deposited Non-`token` ERC20s in `Airdrop` Contract Are Permanently Locked — (`File: core/contracts/Airdrop.sol`)

---

### Summary

`Airdrop.sol` is initialized with a single hardcoded `token` address and contains no mechanism to recover any other ERC20 token accidentally sent to it. Any non-`token` ERC20 transferred to the contract is permanently locked with no on-chain recovery path.

---

### Finding Description

`Airdrop.sol` stores a single immutable token address set at initialization: [1](#0-0) 

All distribution logic exclusively operates on that one address: [2](#0-1) 

The contract inherits `OwnableUpgradeable` but exposes **no** `sweep`, `recoverToken`, `rescueToken`, or `emergencyWithdraw` function. A full grep across all production Solidity files confirms none of these patterns exist anywhere in the codebase. The complete public/external interface of `Airdrop.sol` is:

- `initialize` — sets `token` once
- `registerMerkleRoot` — owner-only, registers weekly roots
- `claim` — distributes `token` to eligible users
- `getClaimed` — view [3](#0-2) 

There is no function that accepts an arbitrary token address and transfers its balance out.

---

### Impact Explanation

Any ERC20 token other than the designated `token` that lands in `Airdrop.sol` — whether by user error, a token migration, a rebasing/wrapper swap, or a misdirected airdrop from a third party — is permanently irrecoverable. The contract holds real token balances loaded for weekly distribution rounds, making it a high-value target for accidental misdirection. The asset delta is the full balance of the mistakenly deposited token, locked forever.

---

### Likelihood Explanation

Moderate. The `Airdrop` contract is a well-known, publicly deployed address that holds tokens. Scenarios include:

- A user or operator accidentally sends the wrong ERC20 (e.g., a stablecoin or governance token) to the airdrop address instead of the intended recipient.
- A token upgrade or migration (e.g., USDC.e → USDC, as already handled elsewhere in `ContractOwner.replaceUsdcEWithUsdc`) results in the old token being sent to the airdrop contract.
- A third-party protocol airdrop targets the contract address by mistake.

None of these require privileged access to trigger; any externally controlled address can send ERC20 tokens to the contract.

---

### Recommendation

Add an owner-restricted token recovery function that explicitly excludes the designated `token` to prevent accidental draining of airdrop funds:

```solidity
function recoverToken(address _token, address to) external onlyOwner {
    require(_token != token, "Cannot recover airdrop token");
    uint256 balance = IERC20(_token).balanceOf(address(this));
    SafeERC20.safeTransfer(IERC20(_token), to, balance);
}
```

This mirrors the pattern already used in `DirectDepositV1.withdraw` and `ContractOwner.withdrawFromDirectDepositV1`, which both accept an arbitrary token address for recovery. [4](#0-3) 

---

### Proof of Concept

1. `Airdrop` is deployed and initialized with `token = 0xTOKEN_A`.
2. An operator or user calls `ERC20(TOKEN_B).transfer(airdropAddress, amount)` — either by mistake or due to a token migration.
3. `TOKEN_B` balance now sits in `Airdrop.sol`.
4. No function in `Airdrop.sol` accepts `TOKEN_B` as a parameter or transfers it out.
5. The owner calls every available function: `registerMerkleRoot` (no token transfer), `claim` (only transfers `TOKEN_A`), `getClaimed` (view). None can move `TOKEN_B`.
6. `TOKEN_B` is permanently locked.

### Citations

**File:** core/contracts/Airdrop.sol (L11-96)
```text
contract Airdrop is OwnableUpgradeable, IAirdrop {
    address internal token;
    address internal sanctions;
    uint32 internal pastWeeks;

    mapping(uint32 => bytes32) internal merkleRoots;
    mapping(uint32 => mapping(address => uint256)) internal claimed;

    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
    }

    function initialize(address _token, address _sanctions)
        external
        initializer
    {
        __Ownable_init();
        token = _token;
        sanctions = _sanctions;
    }

    function registerMerkleRoot(uint32 week, bytes32 merkleRoot)
        external
        onlyOwner
    {
        pastWeeks += 1;
        require(week == pastWeeks, "Invalid week provided.");
        merkleRoots[week] = merkleRoot;
    }

    function _verifyProof(
        uint32 week,
        address sender,
        uint256 totalAmount,
        bytes32[] calldata proof
    ) internal {
        require(claimed[week][sender] == 0, "Already claimed.");
        require(
            merkleRoots[week] != bytes32(0),
            "Week hasn't been registered."
        );
        require(
            !ISanctionsList(sanctions).isSanctioned(sender),
            "address is sanctioned."
        );
        bytes32 leaf = keccak256(
            bytes.concat(keccak256(abi.encode(sender, totalAmount)))
        );
        bool isValidLeaf = MerkleProof.verify(proof, merkleRoots[week], leaf);
        require(isValidLeaf, "Invalid proof.");
        claimed[week][sender] = totalAmount;
    }

    function _claim(
        uint32 week,
        uint256 totalAmount,
        bytes32[] calldata proof
    ) internal {
        _verifyProof(week, msg.sender, totalAmount, proof);
        SafeERC20.safeTransfer(IERC20(token), msg.sender, totalAmount);
        emit Claim(msg.sender, week, totalAmount);
    }

    function claim(ClaimProof[] calldata claimProofs) external {
        for (uint32 i = 0; i < claimProofs.length; i++) {
            _claim(
                claimProofs[i].week,
                claimProofs[i].totalAmount,
                claimProofs[i].proof
            );
        }
    }

    function getClaimed(address account)
        external
        view
        returns (uint256[] memory)
    {
        uint256[] memory result = new uint256[](pastWeeks + 1);
        for (uint32 week = 1; week <= pastWeeks; week++) {
            result[week] = claimed[week][account];
        }
        return result;
    }
}
```

**File:** core/contracts/DirectDepositV1.sol (L103-106)
```text
    function withdraw(IIERC20Base token) external onlyOwner {
        uint256 balance = token.balanceOf(address(this));
        safeTransfer(token, msg.sender, balance);
    }
```
