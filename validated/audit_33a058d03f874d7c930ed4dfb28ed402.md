### Title
Unclaimed Weekly Airdrop Tokens Permanently Locked with No Recovery Mechanism — (File: `core/contracts/Airdrop.sol`)

---

### Summary

The `Airdrop.sol` contract distributes tokens to eligible users on a per-week basis using Merkle proofs. Once tokens are loaded into the contract and a week's Merkle root is registered, any portion of that week's allocation that goes unclaimed is permanently locked. There is no expiry, sweep, or recovery function of any kind.

---

### Finding Description

The `Airdrop` contract holds a token balance and allows users to claim their weekly allocation by submitting a valid Merkle proof via `claim()`. The `_claim` internal function verifies the proof and transfers `totalAmount` to the caller: [1](#0-0) 

The contract tracks what each address has claimed per week in `claimed[week][sender]`, but it tracks **no aggregate** of total tokens allocated per week versus total tokens disbursed. Crucially, there is no function to recover tokens that remain unclaimed after a week has passed: [2](#0-1) 

The full contract surface — `initialize`, `registerMerkleRoot`, `_verifyProof`, `_claim`, `claim`, `getClaimed` — contains zero sweep, rescue, or expiry logic. Any tokens sitting in the contract that correspond to unclaimed weekly allocations are irrecoverable.

Contrast this with `DirectDepositV1.sol`, which has explicit `withdraw` and `withdrawNative` owner-callable recovery functions: [3](#0-2) 

No equivalent exists in `Airdrop.sol`.

---

### Impact Explanation

Tokens pre-loaded into the `Airdrop` contract for a given week that are not claimed — due to user inaction, lost keys, all eligible addresses being sanctioned, or simply low participation — are permanently locked in the contract with no path to recovery or redistribution. The magnitude scales with the size of each weekly allocation and the number of weeks with low claim rates. This is a direct, concrete asset loss.

---

### Likelihood Explanation

Airdrop non-participation is a well-documented real-world phenomenon. Sanctioned addresses are explicitly blocked from claiming: [4](#0-3) 

Any tokens allocated to sanctioned addresses in the Merkle tree are unclaimable by construction and permanently locked. Additionally, users with lost keys or those unaware of the airdrop will not claim. Over many weeks, the cumulative locked amount can be substantial.

---

### Recommendation

Add an owner-callable recovery function that allows sweeping unclaimed tokens after a configurable expiry period (e.g., N weeks after a given week's Merkle root was registered). Example:

```solidity
function recoverUnclaimed(address recipient, uint256 amount) external onlyOwner {
    SafeERC20.safeTransfer(IERC20(token), recipient, amount);
}
```

Alternatively, track total tokens allocated per week and allow recovery of `allocated - claimed` after an expiry window.

---

### Proof of Concept

1. Owner calls `registerMerkleRoot(1, root)` for week 1, where the Merkle tree allocates 100,000 tokens total across eligible addresses.
2. Owner transfers 100,000 tokens to the `Airdrop` contract.
3. Week 1 passes. 40,000 tokens are claimed by active users.
4. The remaining 60,000 tokens — corresponding to sanctioned addresses, lost-key holders, or non-participants — remain in the contract.
5. No function exists to recover or redirect these 60,000 tokens. They are permanently locked. [5](#0-4) [6](#0-5)

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

**File:** core/contracts/DirectDepositV1.sol (L103-112)
```text
    function withdraw(IIERC20Base token) external onlyOwner {
        uint256 balance = token.balanceOf(address(this));
        safeTransfer(token, msg.sender, balance);
    }

    function withdrawNative() external onlyOwner {
        uint256 balance = address(this).balance;
        (bool success, ) = msg.sender.call{value: balance}("");
        require(success, "Failed to transfer native token to owner");
    }
```
