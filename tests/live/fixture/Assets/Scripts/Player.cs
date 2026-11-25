using UnityEngine;

public class Player : MonoBehaviour
{
    [SerializeField] private float _speed = 5f;
    [SerializeField] private Rigidbody _body;

    public int Health { get; private set; } = 100;

    private void Update()
    {
        Vector3 move = new Vector3(Input.GetAxis("Horizontal"), 0f, 0f);
        transform.Translate(move * _speed * Time.deltaTime);
        _body.AddForce(Vector3.up);
    }

    public void Damage(int amount)
    {
        Health = Mathf.Clamp(Health - amount, 0, 100);
        if (Health == 0)
            Die();
    }

    protected virtual void Die()
    {
        Destroy(gameObject);
    }
}
